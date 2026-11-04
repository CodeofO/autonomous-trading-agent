import type { QueryClient } from "@tanstack/react-query";
import { eventsUrl, getToken, marketApi } from "../api/client";
import type { AgentProgress, AuditEvent, ChatMsg, MarketCode, Role } from "../api/types";
import { invalidateTargets, PendingTargets, targetsFor, type Targets, type WsMsg } from "./invalidation";
import { insertAudit, insertMessage, mergeAudit, mergeMessages } from "./merge";

/*
 * 실시간 이벤트 저장소 — 시장마다 웹소켓 하나 (국장 /ws/events · 미장 /ws/events?market=US).
 *
 * 화면은 필요한 조각만 구독한다(useSyncExternalStore, live/hooks.ts): 연결 상태만 보는 머리말·메뉴는
 * 채팅 메시지·진행 표시가 와도 다시 그려지지 않는다.
 *
 * 서버에서 처음 받아 오는 것(채팅 기록·진행 상태, 감사 로그)은 그것을 보여 주는 화면이 있을 때만,
 * 연결마다 한 번 받는다:
 *  - 웹소켓이 열린 뒤에 받는다 → 받아 온 기록과 실시간 메시지 사이에 빈틈이 없다 (그래서 두 번 받을 필요가 없다)
 *  - 연결이 늦으면(OPEN_GRACE_MS) 먼저 받아 화면을 채우고, 열린 뒤 한 번 더 맞춘다
 *  - 끊겼다 다시 열리면 놓친 것을 맞추려고 다시 받는다 (보여 주는 화면이 없으면 다음에 열 때)
 */

export interface Typing {
  role: Role;
  label: string;
}

export interface MarketEvents {
  connected: boolean;
  /** 채팅 (id 오름차순, 최근 800개) */
  messages: ChatMsg[];
  /** 채팅 기록을 서버에서 한 번이라도 받아 봤는지 (받는 중 '자리 표시'와 '소식 없음'을 가른다) */
  chatReady: boolean;
  /** 감사 로그 (id 내림차순, 최근 300개) */
  audit: AuditEvent[];
  /** 감사 로그를 서버에서 한 번이라도 받아 봤는지 (받는 중 자리 표시와 '기록 없음'을 가른다) */
  auditReady: boolean;
  typing: Typing | null;
  progress: AgentProgress | null;
}

export const EMPTY_EVENTS: MarketEvents = Object.freeze({
  connected: false,
  messages: [],
  chatReady: false,
  audit: [],
  auditReady: false,
  typing: null,
  progress: null,
}) as MarketEvents;

/** 서버에서 받아 오는 묶음: 채팅(기록 + 진행 상태) · 감사 로그 */
export type Resource = "chat" | "audit";
const RESOURCES: Resource[] = ["chat", "audit"];

/** 웹소켓이 열리기를 기다리는 최대 시간 — 넘으면 먼저 받아 화면을 채운다 */
export const OPEN_GRACE_MS = 300;
/** 잦은 이벤트에 캐시를 매번 다시 받지 않도록 묶는 시간 */
export const INVALIDATE_DELAY_MS = 600;
const PING_MS = 30_000;

type Listener = () => void;

export class MarketStream {
  private state: MarketEvents = EMPTY_EVENTS;
  private listeners = new Set<Listener>();
  private needs: Record<Resource, number> = { chat: 0, audit: 0 };
  /** 지금 연결 기준으로 서버와 맞췄는지 (끊기면 false → 다시 열릴 때 맞춘다) */
  private synced: Record<Resource, boolean> = { chat: false, audit: false };
  private loading: Record<Resource, boolean> = { chat: false, audit: false };
  private enabled = false;
  private ws: WebSocket | null = null;
  private open = false;
  /** 연결이 열릴 때마다 1씩 — 받아 오는 동안 연결이 바뀌었는지 본다 */
  private epoch = 0;
  private retry = 0;
  private timers = { grace: 0, reconnect: 0, ping: 0, invalidate: 0 };
  /** 진행 이벤트를 받을 때마다 증가 — 그보다 늦게 도착한 GET 응답(옛 상태)은 버린다 */
  private progressSeq = 0;
  private pending = new PendingTargets();

  constructor(
    readonly market: MarketCode,
    private readonly qc: QueryClient | null,
  ) {}

  // ------------------------------------------------------------ 구독 (useSyncExternalStore)
  getState = (): MarketEvents => this.state;

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  get connected() {
    return this.state.connected;
  }

  private set(patch: Partial<MarketEvents>) {
    let changed = false;
    for (const k of Object.keys(patch) as (keyof MarketEvents)[]) {
      if (this.state[k] !== patch[k]) changed = true;
    }
    if (!changed) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  /**
   * 이 묶음을 보여 주는 화면이 생겼다 — 아직 서버와 맞추지 않았으면 받는다. 돌려주는 함수로 놓는다.
   * (화면이 사라져도 받은 내용과 실시간 갱신은 그대로 둔다 — 다시 열면 바로 보인다)
   */
  acquire(res: Resource): () => void {
    this.needs[res]++;
    this.ensure(res);
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.needs[res]--;
    };
  }

  private ensure(res: Resource) {
    if (!this.enabled || this.synced[res] || this.loading[res]) return;
    if (this.open) {
      this.load(res);
      return;
    }
    // 열리기를 잠깐 기다린다 (열리면 onOpen 이 받는다)
    if (!this.timers.grace) {
      this.timers.grace = window.setTimeout(() => {
        this.timers.grace = 0;
        for (const r of RESOURCES) if (this.needs[r] > 0 && !this.synced[r] && !this.loading[r]) this.load(r);
      }, OPEN_GRACE_MS);
    }
  }

  private load(res: Resource) {
    const api = marketApi(this.market);
    const epoch = this.epoch;
    const afterOpen = this.open;
    // 받은 기록은 연결이 바뀌었어도 합친다 (id 로 겹침 제거). 꺼진 시장이면 버린다
    const current = () => this.enabled;
    this.loading[res] = true;
    const done = (ok: boolean) => {
      if (this.epoch !== epoch) return; // 그 사이 다시 연결됨 — 새 연결이 다시 받는다
      this.loading[res] = false;
      // 열린 뒤에 받은 것만 '맞춤'으로 친다 (열리기 전에 받은 것은 열리면 한 번 더)
      if (ok && afterOpen && this.open) this.synced[res] = true;
    };
    if (res === "chat") {
      const seq = this.progressSeq;
      const chat = api
        .get<ChatMsg[]>("/api/chat?limit=200")
        .then((m) => {
          if (current()) this.set({ messages: mergeMessages(this.state.messages, m), chatReady: true });
          return true;
        })
        .catch(() => {
          if (current()) this.set({ chatReady: true });
          return false;
        });
      const progress = api
        .get<AgentProgress>("/api/agent/progress")
        .then((p) => {
          if (current() && seq === this.progressSeq) this.applyProgress(p);
          return true;
        })
        .catch(() => false);
      void Promise.all([chat, progress]).then(([a, b]) => done(a && b));
    } else {
      void api
        .get<AuditEvent[]>("/api/events?limit=200")
        .then((a) => {
          if (current()) this.set({ audit: mergeAudit(this.state.audit, a), auditReady: true });
          done(true);
        })
        .catch(() => {
          if (current()) this.set({ auditReady: true });
          done(false);
        });
    }
  }

  /**
   * 보여 주는 묶음(채팅·감사 로그)을 서버에서 다시 받아 맞춘다 — 휴대폰 '당겨서 새로고침'.
   * 받은 기록과 합치므로(id 로 겹침 제거) 실시간으로 받은 메시지는 그대로 남는다
   */
  resync(): Promise<void> {
    if (!this.enabled) return Promise.resolve();
    const api = marketApi(this.market);
    const jobs: Promise<unknown>[] = [];
    if (this.needs.chat > 0) {
      jobs.push(
        api
          .get<ChatMsg[]>("/api/chat?limit=200")
          .then((m) => this.enabled && this.set({ messages: mergeMessages(this.state.messages, m) })),
      );
    }
    if (this.needs.audit > 0) {
      jobs.push(
        api
          .get<AuditEvent[]>("/api/events?limit=200")
          .then((a) => this.enabled && this.set({ audit: mergeAudit(this.state.audit, a) })),
      );
    }
    return Promise.allSettled(jobs).then(() => undefined);
  }

  private applyProgress(p: AgentProgress) {
    this.set({ progress: p, typing: p.typing ? { role: "analyst", label: p.typing } : null });
  }

  // ------------------------------------------------------------ 연결
  setEnabled(on: boolean) {
    if (on === this.enabled) return;
    this.enabled = on;
    if (on) {
      this.connect();
      for (const r of RESOURCES) if (this.needs[r] > 0) this.ensure(r);
    } else {
      this.stop();
      this.state = EMPTY_EVENTS;
      this.synced = { chat: false, audit: false };
      this.listeners.forEach((fn) => fn());
    }
  }

  /** 연결을 닫는다 (받은 내용은 그대로 — 다시 켜면 이어서 쓴다) */
  stop() {
    this.enabled = false;
    this.epoch++;
    this.loading = { chat: false, audit: false };
    this.synced = { chat: false, audit: false };
    for (const k of Object.keys(this.timers) as (keyof typeof this.timers)[]) {
      window.clearTimeout(this.timers[k]);
      window.clearInterval(this.timers[k]);
      this.timers[k] = 0;
    }
    this.pending.clear();
    const ws = this.ws;
    this.ws = null;
    this.open = false;
    ws?.close();
    if (this.state.connected) this.set({ connected: false });
  }

  private connect() {
    if (!this.enabled) return;
    const ws = new WebSocket(eventsUrl(this.market, getToken()));
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.retry = 0;
      this.open = true;
      this.epoch++;
      window.clearTimeout(this.timers.grace);
      this.timers.grace = 0;
      this.loading = { chat: false, audit: false };
      this.synced = { chat: false, audit: false };
      this.set({ connected: true });
      // 보여 주는 화면이 있는 묶음만 지금 맞춘다 (나머지는 그 화면을 열 때)
      for (const r of RESOURCES) if (this.needs[r] > 0) this.load(r);
      this.timers.ping = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("PING");
      }, PING_MS);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.open = false;
      this.synced = { chat: false, audit: false };
      window.clearInterval(this.timers.ping);
      this.timers.ping = 0;
      this.set({ connected: false });
      if (this.enabled) {
        this.timers.reconnect = window.setTimeout(
          () => {
            this.timers.reconnect = 0;
            this.connect();
          },
          Math.min(10_000, 500 * 2 ** this.retry++),
        );
      }
    };
    ws.onmessage = (ev: MessageEvent<string>) => {
      if (this.ws !== ws) return;
      let msg: WsMsg;
      try {
        msg = JSON.parse(ev.data) as WsMsg;
      } catch {
        return;
      }
      this.handle(msg);
    };
  }

  /** 웹소켓 한 줄 처리 (테스트에서도 직접 부른다) */
  handle(msg: WsMsg) {
    if (msg.channel === "chat") {
      const m = msg.data;
      const patch: Partial<MarketEvents> = { messages: insertMessage(this.state.messages, m) };
      if (m.role === "analyst" && m.kind !== "tool") patch.typing = null;
      this.set(patch);
    } else if (msg.channel === "audit") {
      this.set({ audit: insertAudit(this.state.audit, msg.data) });
    } else if (msg.channel === "typing") {
      const t = msg.data;
      this.set({ typing: t.active ? { role: t.role, label: t.label } : null });
    } else if (msg.channel === "progress") {
      // 자주 오는 이벤트라 캐시는 다시 받지 않는다
      this.progressSeq++;
      this.applyProgress(msg.data);
    }
    const t = targetsFor(msg);
    if (t) this.schedule(t);
  }

  /** 이벤트가 바꾼 캐시만 모아 두었다가 한 번에 다시 받게 한다 (live/invalidation.ts 의 표) */
  private schedule(t: Targets) {
    if (!this.qc) return;
    this.pending.add(t);
    if (this.timers.invalidate) return;
    this.timers.invalidate = window.setTimeout(() => {
      this.timers.invalidate = 0;
      if (this.pending.empty || !this.qc) return;
      const snapshot = new PendingTargets();
      snapshot.add({ market: [...this.pending.market], global: [...this.pending.global] });
      this.pending.clear();
      void invalidateTargets(this.qc, this.market, snapshot);
    }, INVALIDATE_DELAY_MS);
  }
}

/** 두 시장의 실시간 저장소 (EventsProvider 하나에 하나) */
export class EventsStore {
  readonly streams: Record<MarketCode, MarketStream>;
  constructor(qc: QueryClient | null) {
    this.streams = { KR: new MarketStream("KR", qc), US: new MarketStream("US", qc) };
  }
  stream(m: MarketCode): MarketStream {
    return this.streams[m];
  }
  /** 이 시장(없으면 아무 시장이든) 웹소켓이 연결돼 있는지 — 연결돼 있으면 폴링을 늦춘다 */
  isLive(m: MarketCode | null): boolean {
    return m ? this.streams[m].connected : this.streams.KR.connected || this.streams.US.connected;
  }
  stopAll() {
    this.streams.KR.stop();
    this.streams.US.stop();
  }
}

/** EventsProvider 밖(테스트에서 화면 하나만 그릴 때 등): 연결하지 않고 늘 빈 상태 */
export const OFFLINE_STORE = new EventsStore(null);
