import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, errorText, get, post, put } from "../api/client";
import type {
  PushPrefs,
  PushStatus,
  PushSubscribeResult,
  PushTestResult,
  RiskConfig,
  Strategy,
} from "../api/types";
import {
  currentSubscription,
  deviceLabel,
  getDeviceId,
  notificationPermission,
  pushErrorText,
  pushSupport,
  readEnv,
  requestPermission,
  setDeviceId,
  subscribePush,
  unsubscribePush,
} from "../push";
import { fmtDateTime } from "../utils/format";

export function Settings() {
  return (
    <div className="stack">
      <PushSettings />
      <AgentSettings />
      <RiskSettings />
      <div className="two-col">
        <StrategySettings />
        <Watchlist />
      </div>
    </div>
  );
}

const PUSH_PREFS: { key: keyof PushPrefs; label: string; hint: string }[] = [
  { key: "fills", label: "체결", hint: "매수·매도 주문이 체결되면" },
  { key: "risk", label: "리스크 차단", hint: "리스크 규칙이 주문을 막으면" },
  { key: "alerts", label: "킬 스위치·오류", hint: "엔진 정지, 연결·실행 오류" },
  { key: "agent", label: "에이전트 판단 완료·리포트", hint: "매 판단 세션·리포트마다 (자주 올 수 있어요)" },
];

function testResultText(r: PushTestResult): string {
  const extra = [r.failed ? `실패 ${r.failed}` : "", r.removed ? `만료된 기기 ${r.removed}개 정리` : ""]
    .filter(Boolean)
    .join(" · ");
  const head = r.sent > 0 ? `보냈어요 ✓ (${r.sent}대)` : "보내지 못했어요";
  return extra ? `${head} · ${extra}` : head;
}

/** 휴대폰 알림(웹 푸시). 이 기기 켜기/끄기, 받을 알림 종류, 등록된 기기 목록 */
export function PushSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["push"], queryFn: () => get<PushStatus>("/api/push") });
  const [env] = useState(readEnv);
  const support = pushSupport(env);
  const [permission, setPermission] = useState(notificationPermission);
  // 이 기기 브라우저의 구독 주소 (undefined = 확인 중)
  const [endpoint, setEndpoint] = useState<string | null | undefined>(support === "ok" ? undefined : null);
  const [myId, setMyId] = useState(getDeviceId);
  useEffect(() => {
    if (support !== "ok") return;
    let alive = true;
    currentSubscription().then((s) => alive && setEndpoint(s?.endpoint ?? null));
    return () => {
      alive = false;
    };
  }, [support]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["push"] });
  const remember = (id: string | null) => {
    setDeviceId(id);
    setMyId(id);
  };

  const enable = useMutation({
    // 권한 요청은 클릭 처리 안에서 이미 시작해 넘겨받는다 (iOS 는 사용자 터치 직후에만 권한 창을 띄운다)
    mutationFn: async (asked: Promise<NotificationPermission>) => {
      const perm = await asked;
      setPermission(perm);
      if (perm === "denied") return null; // 아래 '권한 꺼짐' 안내로 바뀐다
      if (perm !== "granted") throw new Error("알림 권한을 허용해야 켤 수 있어요");
      const body = await subscribePush(q.data?.public_key ?? "");
      setEndpoint(body.endpoint);
      const label = deviceLabel(navigator.userAgent, navigator.platform, navigator.maxTouchPoints);
      const r = await post<PushSubscribeResult>("/api/push/subscribe", { ...body, label });
      return r.id;
    },
    onSuccess: (id) => {
      if (id) remember(id);
      refresh();
    },
  });
  const disable = useMutation({
    mutationFn: async () => {
      // 이 기기 구독부터 끊는다 — 서버 요청이 실패해도 알림은 더 오지 않는다
      const ep = (await unsubscribePush()) ?? endpoint;
      setEndpoint(null);
      remember(null);
      if (ep) await post("/api/push/unsubscribe", { endpoint: ep });
    },
    onSettled: refresh,
  });
  const test = useMutation({
    mutationFn: (ep: string | null) => post<PushTestResult>("/api/push/test", ep ? { endpoint: ep } : {}),
    onSuccess: (r) => {
      if (r.removed) refresh();
    },
  });
  const savePrefs = useMutation({
    mutationFn: (prefs: PushPrefs) => put<PushPrefs>("/api/push/prefs", prefs),
    onSuccess: (prefs) => qc.setQueryData<PushStatus>(["push"], (old) => old && { ...old, prefs }),
  });
  const remove = useMutation({
    mutationFn: async (id: string) => {
      await del(`/api/push/devices/${encodeURIComponent(id)}`);
      if (id === myId) {
        await unsubscribePush().catch(() => null);
        setEndpoint(null);
        remember(null);
      }
    },
    onSuccess: refresh,
  });

  const data = q.data;
  const devices = data?.devices ?? [];
  const isOn = !!endpoint && !!myId && devices.some((d) => d.id === myId);
  const prefs = savePrefs.isPending ? savePrefs.variables : data?.prefs;
  const busy = enable.isPending || disable.isPending;

  let state: ReactNode = null;
  if (support === "insecure") {
    state = (
      <p className="push-note warn">
        알림은 보안 연결(HTTPS) 주소에서만 켤 수 있어요. 지금 주소는 HTTPS 가 아니에요 — Tailscale 의 HTTPS
        주소(https:// 로 시작)로 접속해서 켜 주세요.
      </p>
    );
  } else if (support === "ios-install") {
    state = (
      <p className="push-note warn">
        iPhone 은 홈 화면에 추가한 앱에서만 알림을 받을 수 있어요. Safari 의 <b>공유 버튼</b> →{" "}
        <b>'홈 화면에 추가'</b>를 누른 뒤, 홈 화면의 아이콘으로 열어서 여기서 켜 주세요. (iOS 16.4 이상)
      </p>
    );
  } else if (support === "unsupported") {
    state = <p className="push-note warn">이 브라우저는 알림을 지원하지 않아요.</p>;
  } else if (permission === "denied") {
    state = (
      <p className="push-note warn">
        {env.ios
          ? "알림 권한이 꺼져 있어요. iPhone 설정 > 알림 > AI 펀드매니저에서 알림을 허용한 뒤 앱을 다시 열어 주세요."
          : "알림 권한이 꺼져 있어요. 브라우저의 사이트 설정 > 알림에서 허용한 뒤 새로고침해 주세요."}
      </p>
    );
  } else if (endpoint === undefined) {
    state = <p className="muted">이 기기 상태 확인 중…</p>;
  } else if (isOn) {
    state = (
      <>
        <p className="ok">이 기기에서 알림을 받고 있어요 ✓</p>
        <div className="btn-row push-actions">
          <button
            className="btn primary"
            disabled={test.isPending}
            onClick={() => test.mutate(endpoint ?? null)}
          >
            테스트 알림 보내기
          </button>
          <button className="btn ghost" disabled={busy} onClick={() => disable.mutate()}>
            이 기기 알림 끄기
          </button>
        </div>
      </>
    );
  } else {
    state = (
      <>
        <p className="muted">이 기기는 아직 알림을 받지 않아요.</p>
        <div className="btn-row push-actions">
          <button
            className="btn primary"
            disabled={busy || !data?.public_key}
            onClick={() => enable.mutate(requestPermission())}
          >
            {enable.isPending ? "켜는 중…" : "알림 켜기"}
          </button>
        </div>
      </>
    );
  }

  return (
    <section className="card push-card">
      <h2>🔔 휴대폰 알림</h2>
      <p className="muted">체결·리스크 차단·킬 스위치 같은 중요한 일을 휴대폰 알림으로 바로 알려 드려요.</p>
      {q.isLoading && <p className="muted">불러오는 중…</p>}
      {q.isError && <p className="error">{errorText(q.error)}</p>}
      {data && !data.available && (
        <p className="push-note warn">
          서버에서 알림을 보낼 수 없어요: {data.reason ?? "서버 설정을 확인해 주세요"}
        </p>
      )}
      {data?.available && (
        <>
          <div className="push-state">
            {state}
            {enable.isError && <p className="error">{pushErrorText(enable.error)}</p>}
            {disable.isError && <p className="error">{pushErrorText(disable.error)}</p>}
            {test.isSuccess && (
              <p className={test.data.sent > 0 ? "ok" : "error"}>{testResultText(test.data)}</p>
            )}
            {test.isError && <p className="error">{errorText(test.error)}</p>}
          </div>

          <div className="push-prefs" role="group" aria-label="받을 알림">
            <h3>받을 알림</h3>
            {PUSH_PREFS.map((p) => (
              <label key={p.key} className="toggle">
                <input
                  type="checkbox"
                  checked={prefs?.[p.key] ?? false}
                  disabled={!prefs}
                  onChange={(e) => prefs && savePrefs.mutate({ ...prefs, [p.key]: e.target.checked })}
                />
                <span>
                  {p.label} <small className="muted">{p.hint}</small>
                </span>
              </label>
            ))}
            {savePrefs.isError && <p className="error">{errorText(savePrefs.error)}</p>}
          </div>

          {devices.length > 0 && (
            <div className="push-devices-wrap">
              <div className="card-head">
                <h3>알림 받는 기기 {devices.length}대</h3>
                {!isOn && (
                  <button className="btn tiny" disabled={test.isPending} onClick={() => test.mutate(null)}>
                    모든 기기에 테스트
                  </button>
                )}
              </div>
              <ul className="watch push-devices">
                {devices.map((d) => (
                  <li key={d.id}>
                    <span className="push-device">
                      <span>
                        {d.label || "이름 없는 기기"}
                        {d.id === myId && isOn && <span className="pill">이 기기</span>}
                      </span>
                      <small className="muted">
                        등록 {fmtDateTime(d.created_at)} · 마지막 전송{" "}
                        {d.last_ok ? fmtDateTime(d.last_ok) : "아직 없음"}
                      </small>
                    </span>
                    <button
                      className="btn tiny ghost"
                      aria-label={`${d.label || "기기"} 삭제`}
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(d.id)}
                    >
                      삭제
                    </button>
                  </li>
                ))}
              </ul>
              {remove.isError && <p className="error">{errorText(remove.error)}</p>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function AgentSettings() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["agent-settings"],
    queryFn: () =>
      get<{ name: string; emoji: string; policy: string; default_policy: string }>("/api/agent/settings"),
  });
  const [form, setForm] = useState({ name: "", emoji: "", policy: "" });
  useEffect(() => {
    if (q.data) setForm({ name: q.data.name, emoji: q.data.emoji, policy: q.data.policy });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => put("/api/agent/settings", form),
    onSuccess: () => qc.invalidateQueries(),
  });
  return (
    <section className="card">
      <h2>🦉 에이전트 (매니저 캐릭터 · 투자 정책)</h2>
      <p className="muted">
        투자 정책은 매 판단 세션의 시스템 프롬프트에 들어가요. 리스크 한도는 아래 설정이 코드로 강제하므로,
        정책에 뭐라고 써도 한도를 넘을 수 없어요.
      </p>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
        className="form"
      >
        <div className="row">
          <label>
            이름
            <input
              value={form.name}
              maxLength={20}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            이모지
            <input
              value={form.emoji}
              maxLength={8}
              onChange={(e) => setForm({ ...form, emoji: e.target.value })}
              className="emoji-input"
            />
          </label>
        </div>
        <label>
          투자 정책
          <textarea
            rows={7}
            value={form.policy}
            onChange={(e) => setForm({ ...form, policy: e.target.value })}
          />
        </label>
        <div className="btn-row">
          <button className="btn primary" disabled={save.isPending}>
            저장
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => q.data && setForm({ ...form, policy: q.data.default_policy })}
          >
            기본 정책으로
          </button>
          {save.isSuccess && <span className="ok">저장됨 ✓</span>}
          {save.isError && <span className="error">{errorText(save.error)}</span>}
        </div>
      </form>
    </section>
  );
}

const RISK_FIELDS: { key: keyof RiskConfig; label: string; pct?: boolean; hint: string }[] = [
  { key: "max_position_weight", label: "종목당 최대 비중", pct: true, hint: "예: 20" },
  { key: "max_order_amount", label: "1회 주문 최대 금액(원)", hint: "예: 1,000,000" },
  { key: "daily_loss_limit", label: "일일 최대 손실", pct: true, hint: "음수, 예: -3 → 당일 신규 매수 중단" },
  { key: "stop_loss", label: "종목별 손절", pct: true, hint: "음수, 예: -5" },
  { key: "take_profit", label: "종목별 익절", pct: true, hint: "양수, 예: 10" },
  { key: "min_order_interval_minutes", label: "같은 종목 최소 매수 간격(분, 매도 제외)", hint: "예: 60" },
  { key: "max_positions", label: "최대 보유 종목 수", hint: "예: 10" },
  { key: "min_cash_ratio", label: "최소 현금 비중", pct: true, hint: "예: 5" },
];

export function validateRisk(v: Record<string, number>): string | null {
  if (!(v.max_position_weight > 0 && v.max_position_weight <= 100))
    return "종목당 최대 비중은 0~100% 사이여야 해요";
  if (!(v.max_order_amount > 0)) return "1회 주문 최대 금액은 0보다 커야 해요";
  if (!(v.daily_loss_limit < 0 && v.daily_loss_limit >= -100)) return "일일 최대 손실은 음수여야 해요";
  if (!(v.stop_loss < 0 && v.stop_loss >= -100)) return "손절은 음수여야 해요";
  if (!(v.take_profit > 0)) return "익절은 양수여야 해요";
  if (!(v.min_order_interval_minutes >= 0)) return "최소 주문 간격은 0 이상이어야 해요";
  if (!(v.max_positions >= 1 && Number.isInteger(v.max_positions)))
    return "최대 보유 종목 수는 1 이상의 정수여야 해요";
  if (!(v.min_cash_ratio >= 0 && v.min_cash_ratio < 100)) return "최소 현금 비중은 0~100% 사이여야 해요";
  return null;
}

export function RiskSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["risk"], queryFn: () => get<{ config: RiskConfig }>("/api/risk") });
  const [vals, setVals] = useState<Record<string, number>>({});
  const [allowBuys, setAllowBuys] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const c = q.data.config;
    const v: Record<string, number> = {};
    for (const f of RISK_FIELDS)
      v[f.key] = f.pct ? +((c[f.key] as number) * 100).toFixed(4) : (c[f.key] as number);
    setVals(v);
    setAllowBuys(c.allow_new_buys);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (body: RiskConfig) => put("/api/risk", body),
    onSuccess: () => qc.invalidateQueries(),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const msg = validateRisk(vals);
    setErr(msg);
    if (msg) return;
    const body = { allow_new_buys: allowBuys } as Record<string, number | boolean>;
    for (const f of RISK_FIELDS) body[f.key] = f.pct ? vals[f.key] / 100 : vals[f.key];
    save.mutate(body as unknown as RiskConfig);
  };
  return (
    <section className="card">
      <h2>🛡️ 리스크 한도</h2>
      <p className="muted">
        모든 주문은 브로커로 가기 전에 이 규칙을 통과해야 해요. 장 운영시간 외 주문과 중복 주문은 항상 막아요.
      </p>
      <form onSubmit={submit} className="form grid-3" aria-label="리스크 설정">
        {RISK_FIELDS.map((f) => (
          <label key={f.key}>
            {f.label}
            {f.pct ? " (%)" : ""}
            <input
              type="number"
              step="any"
              value={vals[f.key] ?? ""}
              placeholder={f.hint}
              aria-label={f.label}
              onChange={(e) => setVals({ ...vals, [f.key]: Number(e.target.value) })}
            />
          </label>
        ))}
        <label className="toggle">
          <input type="checkbox" checked={allowBuys} onChange={(e) => setAllowBuys(e.target.checked)} />
          신규 매수 허용
        </label>
        <div className="btn-row full">
          <button className="btn primary" disabled={save.isPending}>
            저장
          </button>
          {err && <span className="error">{err}</span>}
          {save.isError && <span className="error">{errorText(save.error)}</span>}
          {save.isSuccess && !err && <span className="ok">저장됨 ✓</span>}
        </div>
      </form>
    </section>
  );
}

function StrategySettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["strategies"], queryFn: () => get<Strategy[]>("/api/strategies") });
  return (
    <section className="card">
      <h2>📏 규칙 전략</h2>
      <p className="muted">LLM 이 없을 때 규칙 기반 판단과 백테스트에 쓰는 예시 전략이에요.</p>
      {q.data?.map((s) => <StrategyRow key={s.id} s={s} onSaved={() => qc.invalidateQueries()} />)}
    </section>
  );
}

function StrategyRow({ s, onSaved }: { s: Strategy; onSaved: () => void }) {
  const [params, setParams] = useState(s.params);
  const save = useMutation({
    mutationFn: (body: { enabled?: boolean; params?: Record<string, number> }) =>
      put(`/api/strategies/${s.id}`, body),
    onSuccess: onSaved,
  });
  return (
    <div className="strategy">
      <div className="card-head">
        <b>{s.name}</b>
        <label className="toggle">
          <input
            type="checkbox"
            checked={s.enabled}
            onChange={(e) => save.mutate({ enabled: e.target.checked })}
          />
          사용
        </label>
      </div>
      <div className="row">
        {Object.entries(s.schema.properties).map(([k, spec]) => (
          <label key={k}>
            {k}
            <input
              type="number"
              min={spec.minimum}
              max={spec.maximum}
              value={params[k] ?? ""}
              onChange={(e) => setParams({ ...params, [k]: Number(e.target.value) })}
            />
          </label>
        ))}
        <button className="btn tiny" onClick={() => save.mutate({ params })}>
          적용
        </button>
      </div>
      {save.isError && <p className="error">{errorText(save.error)}</p>}
    </div>
  );
}

function Watchlist() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => get<{ symbol: string; name: string; close: number | null }[]>("/api/watchlist"),
  });
  const [sym, setSym] = useState("");
  const add = useMutation({
    mutationFn: () => post("/api/watchlist", { symbol: sym.trim() }),
    onSuccess: () => {
      setSym("");
      qc.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });
  const remove = useMutation({
    mutationFn: (s: string) => del(`/api/watchlist/${s}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  return (
    <section className="card">
      <h2>⭐ 관심 종목</h2>
      <p className="muted">매니저가 매 세션 포트폴리오와 함께 참고하는 목록이에요.</p>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (sym.trim()) add.mutate();
        }}
      >
        <input
          value={sym}
          onChange={(e) => setSym(e.target.value)}
          placeholder="종목코드 (예: 005930 / MOCK01)"
        />
        <button className="btn tiny">추가</button>
      </form>
      {add.isError && <p className="error">{errorText(add.error)}</p>}
      <ul className="watch">
        {q.data?.map((w) => (
          <li key={w.symbol}>
            <Link to={`/stock/${w.symbol}`}>
              {w.name || w.symbol} <small className="muted">{w.symbol}</small>
            </Link>
            <button className="link" onClick={() => remove.mutate(w.symbol)}>
              삭제
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
