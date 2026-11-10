import { useEffect, useMemo, useState, type CSSProperties, type MouseEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api/client";
import type { ChatMsg, MarketCode, MarketInfo, Overview as OverviewData, OverviewMarket } from "../api/types";
import { AnimatedNumber } from "../components/AnimatedNumber";
import { RelTime } from "../components/RelTime";
import { useChatMessages } from "../live/hooks";
import { MARKET_META, useMarket } from "../market";
import { isMobile } from "../mobile";
import { openStockSheet } from "../ui/overlays";
import {
  ACTION_KO,
  fmtDateTime,
  fmtWhen,
  fmtNum,
  fmtPct,
  fmtHm,
  fmtWon,
  JOB_KO,
  money,
  MONEY_FMT,
  signClass,
} from "../utils/format";
import { DAY_START, mergeFeed, OVERVIEW_KEY, timelinePos, timelineStart } from "../utils/overview";
import "./overview.css";

const ENGINE_KO: Record<string, string> = { RUNNING: "실행 중", PAUSED: "일시정지", KILLED: "정지(킬)" };

const wonRounded = (v: number | null | undefined) => fmtWon(v == null ? null : Math.round(v));

/**
 * 🧭 한눈에 보기 ('전체') — 두 시장을 원화로 합친 총자산, 시장별 카드, 오늘 장 시간표, 두 시장 소식.
 * 카드·소식을 누르면 '전체'는 그대로 두고 그 시장의 화면(트레이딩 룸·자산·종목)을 연다.
 */
export function Overview() {
  const { available, markets } = useMarket();
  const q = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => get<OverviewData>("/api/overview"),
    refetchInterval: 15_000, // 장 밖에서도 평가금이 움직인다 (D-61)
  });
  const data = q.data;

  return (
    <div className="overview" data-testid="overview">
      <TotalCard data={data} error={q.isError && !data} />
      <div className="ov-markets">
        {available.map((code) => (
          <MarketCard
            key={code}
            code={code}
            m={data?.markets.find((x) => x.code === code)}
            info={markets?.find((x) => x.code === code)}
            rate={data?.fx.usdkrw ?? null}
            loading={!data && !q.isError}
          />
        ))}
      </div>
      <SessionTimeline codes={available} markets={markets} />
      <ActivityFeed codes={available} />
    </div>
  );
}

/** 원화로 바꾼 시장 총자산 (환율이 없으면 null) */
const krwOf = (m: OverviewMarket, rate: number | null) =>
  m.currency === "USD" ? (rate ? m.equity * rate : null) : m.equity;

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="ov-skel" aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <i key={i} />
      ))}
    </div>
  );
}

// ---------- 총자산 (원화) ----------
function TotalCard({ data, error }: { data?: OverviewData; error: boolean }) {
  if (!data) {
    return (
      <section className="card ov-total" aria-busy={!error} aria-label="전체 총자산">
        <span className="ov-label">총자산 · 원화 환산</span>
        {error ? <p className="error">한눈에 보기를 불러오지 못했어요.</p> : <Skeleton lines={3} />}
      </section>
    );
  }
  const rate = data.fx.usdkrw;
  const total = data.total_krw.equity;
  const pnl = data.total_krw.pnl_today;
  const base = total != null && pnl != null ? total - pnl : null;
  const pnlPct = base && pnl != null ? (pnl / base) * 100 : null;
  const parts = data.markets.map((m) => ({ code: m.code, krw: krwOf(m, rate) }));
  const sum = parts.reduce((s, p) => s + (p.krw ?? 0), 0);
  const split = parts.every((p) => p.krw != null) && sum > 0;

  return (
    <section className="card ov-total" aria-label="전체 총자산">
      <div className="ov-total-main">
        <span className="ov-label">총자산 · 원화 환산</span>
        <b className="ov-total-value" data-testid="ov-total">
          <AnimatedNumber value={total} format={wonRounded} />
        </b>
        <span className={`ov-total-pnl ${signClass(pnl)}`} data-testid="ov-total-pnl">
          오늘 {money(pnl == null ? null : Math.round(pnl), "KRW", { sign: true })}
          {pnlPct != null && ` (${fmtPct(pnlPct)})`}
        </span>
        <small className="muted ov-fx" data-testid="ov-fx">
          {rate
            ? `환율 1달러 = ${fmtNum(rate, 2)}원${data.fx.at ? ` · ${fmtDateTime(data.fx.at)} 기준` : ""}`
            : "환율을 받지 못해 미장은 합계에서 빠졌어요"}
        </small>
      </div>
      {split && (
        <div className="ov-split" aria-label="시장별 비중">
          <div className="ov-split-bar">
            {parts.map((p) => (
              <i
                key={p.code}
                className={`mkt-${p.code.toLowerCase()}`}
                style={{ width: `${((p.krw ?? 0) / sum) * 100}%` }}
              />
            ))}
          </div>
          <ul className="ov-split-legend">
            {parts.map((p) => (
              <li key={p.code} data-testid={`ov-split-${p.code}`}>
                <span className={`sw mkt-${p.code.toLowerCase()}`} />
                {MARKET_META[p.code].name} <b>{(((p.krw ?? 0) / sum) * 100).toFixed(1)}%</b>
                <small className="muted">{fmtWon(Math.round(p.krw ?? 0))}</small>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ---------- 시장 카드 ----------
/** 장 상태 한마디: 장중 · 개장 전(…시 개장) · 마감(다음 개장 …) */
function marketState(info: MarketInfo | undefined, now: number): { text: string; open: boolean } | null {
  if (!info) return null;
  if (info.open_now) return { text: "장중", open: true };
  const s = info.session;
  if (s && now < Date.parse(s.open)) return { text: `개장 전 · ${fmtWhen(s.open, now)} 개장`, open: false };
  const next = info.next_session_open;
  return { text: next ? `마감 · ${fmtWhen(next, now)} 개장` : "마감", open: false };
}

function MarketCard({
  code,
  m,
  info,
  rate,
  loading,
}: {
  code: MarketCode;
  m: OverviewMarket | undefined;
  info: MarketInfo | undefined;
  rate: number | null;
  loading: boolean;
}) {
  const { focusMarket } = useMarket();
  const meta = MARKET_META[code];
  const cur = meta.currency;
  const now = useNow(60_000);
  const state = marketState(info, now);
  const go = () => focusMarket(code);
  // 휴대폰에서 보유 종목을 누르면 화면을 옮기지 않고 빠른 보기 시트
  const quick = (e: MouseEvent, p: OverviewMarket["top_positions"][number]) => {
    if (!isMobile()) return go();
    e.preventDefault();
    openStockSheet({
      symbol: p.symbol,
      name: p.name,
      market: code,
      position: { pnl_pct: p.pnl_pct, weight_pct: p.weight },
    });
  };

  return (
    <section
      className={`card ov-market mkt-${code.toLowerCase()}`}
      aria-label={`${meta.name} 요약`}
      data-testid={`ov-market-${code}`}
    >
      <div className="ov-market-head">
        <h2>
          <span aria-hidden>{meta.flag}</span> {meta.name}
          {code === "US" && <span className="mkt-paper">모의</span>}
        </h2>
        {state && (
          <span className={`ov-state ${state.open ? "open" : ""}`}>
            <span className={`mkt-dot ${state.open ? "open" : ""}`} aria-hidden />
            {state.text}
          </span>
        )}
        {m && (
          <span className={`status-pill ${m.engine_status.toLowerCase()}`}>
            엔진 {ENGINE_KO[m.engine_status] ?? m.engine_status}
          </span>
        )}
      </div>

      {!m ? (
        loading ? (
          <Skeleton lines={4} />
        ) : (
          <p className="muted">이 시장 요약을 받지 못했어요.</p>
        )
      ) : (
        <>
          <div className="ov-eq">
            <b data-testid={`ov-equity-${code}`}>
              <AnimatedNumber value={m.equity} format={MONEY_FMT[cur]} />
            </b>
            {cur === "USD" && rate ? (
              <small className="muted">≈ {fmtWon(Math.round(m.equity * rate))}</small>
            ) : null}
          </div>
          <div className="ov-figs">
            <span className={signClass(m.pnl_today)}>
              오늘 {money(m.pnl_today, cur, { sign: true })} ({fmtPct(m.pnl_today_pct)})
            </span>
            <span className={signClass(m.total_return_pct)}>누적 {fmtPct(m.total_return_pct)}</span>
            <span className="muted">
              현금 {money(m.cash, cur)} · 보유 {m.positions}종목
            </span>
          </div>

          {m.top_positions.length === 0 ? (
            <p className="muted ov-empty">보유 종목이 없어요. 매니저가 기회를 찾는 중이에요 👀</p>
          ) : (
            <ul className="ov-pos" aria-label={`${meta.name} 보유 상위`}>
              {m.top_positions.slice(0, 5).map((p) => (
                <li key={p.symbol}>
                  <Link
                    to={`/stock/${p.symbol}`}
                    onClick={(e) => quick(e, p)}
                    className="ov-pos-name"
                    title={p.symbol}
                  >
                    {p.name || p.symbol}
                  </Link>
                  <span className="ov-bar" aria-hidden>
                    <i style={{ width: `${Math.max(0, Math.min(100, p.weight))}%` }} />
                  </span>
                  <span className="ov-pos-w">{p.weight.toFixed(1)}%</span>
                  <span className={`ov-pos-p ${signClass(p.pnl_pct)}`}>{fmtPct(p.pnl_pct)}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="ov-manager">
            {m.agent_running ? (
              <span className="pill live">판단 중…</span>
            ) : (
              <span className="pill">대기</span>
            )}
            {m.next_run ? (
              <span className="muted">
                다음 {JOB_KO[m.next_run.name] ?? m.next_run.name}{" "}
                <time dateTime={m.next_run.at}>{fmtWhen(m.next_run.at, now)}</time>
              </span>
            ) : (
              <span className="muted">예약된 판단 없음</span>
            )}
          </div>

          {m.last_session ? (
            <Link to="/room" onClick={go} className="ov-last" data-testid={`ov-last-${code}`}>
              <small className="muted">
                마지막 세션 #{m.last_session.run_id} · {fmtDateTime(m.last_session.at)}
              </small>
              <p>{m.last_session.summary || "요약 없음"}</p>
            </Link>
          ) : (
            <p className="muted ov-last-none">아직 판단 세션이 없어요.</p>
          )}

          <div className="btn-row ov-actions">
            <Link to="/room" onClick={go} className="btn tiny">
              💬 {meta.name} 트레이딩 룸
            </Link>
            <Link to="/portfolio" onClick={go} className="btn tiny ghost">
              📒 자산
            </Link>
          </div>
        </>
      )}
    </section>
  );
}

// ---------- 오늘 장 시간 (KST) ----------
/** 지금 시각 (주기마다 갱신) */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

const TICKS = [6, 9, 12, 15, 18, 21, 0, 3];

function untilText(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  const h = Math.floor(m / 60);
  return h ? `${h}시간 ${m % 60}분 후` : `${m}분 후`;
}

interface TimelineRow {
  code: MarketCode;
  info: MarketInfo | undefined;
  /** 시간표 위 막대 (정규장 정보가 없으면 null) */
  bar: { left: number; width: number; phase: "open" | "next" | "done"; text: string; title: string } | null;
  note: string;
}

function timelineRows(codes: MarketCode[], markets: MarketInfo[] | undefined, now: number): TimelineRow[] {
  return codes.map((code) => {
    const info = markets?.find((x) => x.code === code);
    // 시간표 칸(06:00 → 다음날 06:00) 시작 전에 끝난 장(한국 아침의 미장 '현지 오늘' = 지난밤 장)은
    // 칸 밖이라 다음 장을 그린다. 다음 장도 칸 밖(주말·휴장)이면 막대 없이 휴장.
    const from = timelineStart(now);
    let s = info?.session ?? null;
    if ((!s || Date.parse(s.close) <= from) && info?.next_session) s = info.next_session;
    const open = s ? Date.parse(s.open) : NaN;
    const close = s ? Date.parse(s.close) : NaN;
    const ok =
      !!s &&
      Number.isFinite(open) &&
      Number.isFinite(close) &&
      close > open &&
      close > from &&
      open < from + 86_400_000;
    const upcoming = ok && now < open;
    const note = !info
      ? ""
      : info.open_now
        ? "장중"
        : upcoming
          ? `${untilText(open - now)} 개장`
          : ok
            ? "마감"
            : `휴장${info.next_session_open ? ` · ${fmtWhen(info.next_session_open, now)} 개장` : ""}`;
    if (!ok || !s) return { code, info, bar: null, note };
    // 미장은 자정을 넘긴다 (예: 23:30–06:00) — 06:00 에서 시작하는 시간표 안에 그대로 들어간다
    const left = timelinePos(open);
    const width = Math.min(((close - open) / 60_000 / 1440) * 100, 100 - left);
    const text = `${fmtHm(s.open)}–${fmtHm(s.close)}`;
    return {
      code,
      info,
      note,
      bar: {
        left,
        width,
        phase: info?.open_now ? "open" : upcoming ? "next" : "done",
        text,
        title: `${s.day} 정규장 ${text} (한국 시각)`,
      },
    };
  });
}

function SessionTimeline({ codes, markets }: { codes: MarketCode[]; markets: MarketInfo[] | undefined }) {
  const now = useNow(30_000);
  const rows = timelineRows(codes, markets, now);
  return (
    <section className="card ov-timeline" aria-label="오늘 장 시간 (한국 시각)">
      <div className="card-head">
        <h2>오늘 장 시간</h2>
        <small className="muted">한국 시각 · 06:00 → 다음날 06:00</small>
      </div>
      <div className="ov-tl">
        <div className="ov-tl-names">
          {rows.map((r) => (
            <span className="ov-tl-name" key={r.code}>
              {MARKET_META[r.code].name}
              <small className={r.info?.open_now ? "ok" : "muted"}>{r.note}</small>
            </span>
          ))}
        </div>
        <div className="ov-tl-lanes" style={{ "--now": `${timelinePos(now)}%` } as CSSProperties}>
          {rows.map((r) => (
            <div className="ov-tl-track" key={r.code} data-testid={`ov-tl-${r.code}`}>
              {r.bar && (
                <span
                  className={`ov-tl-bar mkt-${r.code.toLowerCase()} ${r.bar.phase}`}
                  style={{ left: `${r.bar.left}%`, width: `${r.bar.width}%` }}
                  title={r.bar.title}
                >
                  {r.bar.text}
                </span>
              )}
            </div>
          ))}
          <div className="ov-tl-axis" aria-hidden>
            {TICKS.map((h) => (
              <span key={h} style={{ left: `${(((h * 60 - DAY_START + 1440) % 1440) / 1440) * 100}%` }}>
                {String(h).padStart(2, "0")}
              </span>
            ))}
          </div>
          <span
            className="ov-tl-now"
            data-testid="ov-now"
            title={`지금 ${fmtHm(new Date(now).toISOString())}`}
          >
            <b>지금</b>
          </span>
        </div>
      </div>
    </section>
  );
}

// ---------- 두 시장 소식 ----------
/** 리포트·세션 정리 본문 앞의 아이콘은 이미 따로 보여 주므로 뺀다 */
const LEAD_ICON = /^(?:\p{Extended_Pictographic}\uFE0F?\s*)+/u;

function feedText(msg: ChatMsg): { icon: string; title: string; body?: string } {
  const p = msg.payload;
  if (msg.kind === "decision") {
    const action = String(p.action ?? "");
    const qty = Number(p.quantity) || 0;
    const conf = Math.round((Number(p.confidence) || 0) * 100);
    return {
      icon: action === "BUY" ? "🔴" : "🔵",
      title: `${ACTION_KO[action] ?? action} ${String(p.name || p.symbol || "")}${qty ? ` ${fmtNum(qty)}주` : ""}`,
      body: `확신도 ${conf}%`,
    };
  }
  if (msg.kind === "fill") return { icon: "💸", title: msg.content.replace(/^💸\s*/u, "") };
  return {
    icon: msg.role === "system" ? "📊" : "🏁",
    title: msg.role === "system" ? "마감 리포트" : "세션 정리",
    body: msg.content.replace(LEAD_ICON, ""),
  };
}

function ActivityFeed({ codes }: { codes: MarketCode[] }) {
  const { focusMarket } = useMarket();
  // 두 시장의 채팅 메시지만 구독 (진행·입력 중 표시는 이 화면에 없다)
  const kr = useChatMessages("KR");
  const us = useChatMessages("US");
  const hasUS = codes.includes("US");
  const items = useMemo(
    () =>
      mergeFeed([
        { code: "KR", messages: kr.messages },
        ...(hasUS ? [{ code: "US" as const, messages: us.messages }] : []),
      ]),
    [kr.messages, us.messages, hasUS],
  );
  // 소식을 아직 받는 중이면 자리 표시 ('소식 없음'을 잠깐 보여 주지 않는다)
  const loaded = kr.ready && (!hasUS || us.ready);
  const waiting = items.length === 0 && !loaded;

  return (
    <section className="card ov-feed" aria-label="두 시장 소식">
      <div className="card-head">
        <h2>최근 소식</h2>
        <small className="muted">결정·체결·세션 정리 · 누르면 그 시장 트레이딩 룸으로</small>
      </div>
      {items.length === 0 ? (
        waiting ? (
          <Skeleton lines={3} />
        ) : (
          <p className="muted ov-empty">아직 소식이 없어요. 판단 세션이 돌면 여기에 모여요.</p>
        )
      ) : (
        <ul className="ov-feed-list">
          {items.map(({ code, msg }) => {
            const t = feedText(msg);
            return (
              <li key={`${code}-${msg.id}`}>
                <Link
                  to="/room"
                  onClick={() => focusMarket(code)}
                  className="ov-feed-item"
                  data-testid="ov-feed-item"
                >
                  <span className={`mkt-badge mkt-${code.toLowerCase()}`}>{MARKET_META[code].name}</span>
                  <span className="ov-feed-icon" aria-hidden>
                    {t.icon}
                  </span>
                  <span className="ov-feed-text">
                    <b>{t.title}</b>
                    {t.body && <span className="ov-feed-body">{t.body}</span>}
                  </span>
                  <RelTime iso={msg.ts} className="muted" />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
