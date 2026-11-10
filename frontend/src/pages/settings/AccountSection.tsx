import { useState, type MouseEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, type MarketApi } from "../../api/client";
import type { MarketCode } from "../../api/types";
import { SkeletonLines } from "../../components/Skeleton";
import { usePortfolio, useSystem } from "../../hooks";
import { useConnected } from "../../live/hooks";
import { MARKET_META, useApi, useCurrency } from "../../market";
import { isMobile } from "../../mobile";
import { openPalette, openStockSheet } from "../../ui/overlays";
import { toast } from "../../ui/toast";
import { fmtPct, money, signClass } from "../../utils/format";
import { llmChipText } from "../../utils/llm";
import { SectionGate } from "./SectionGate";

interface WatchItem {
  symbol: string;
  name: string;
  close: number | null;
}

const watchlistQuery = (api: MarketApi) => ({
  queryKey: api.key("watchlist"),
  queryFn: () => api.get<WatchItem[]>("/api/watchlist"),
});

/** 🏦 계좌·데이터 — 지금 고른 시장의 모의계좌, 데이터 연결 상태, 관심 종목, LLM 비용 화면 */
export function AccountSection() {
  const api = useApi();
  const sys = useSystem();
  const watch = useQuery(watchlistQuery(api));
  return (
    <SectionGate
      ready={!sys.isPending && !watch.isPending}
      heights={[200, 240, 260]}
      label="계좌·데이터 불러오는 중"
    >
      <div className="stack">
        <PaperAccount />
        <DataStatus />
        <Watchlist />
        <Link to="/costs" className="card link-card settings-link-card">
          <b>💸 LLM 요금 화면 →</b>
          <small className="muted">호출마다 어느 모델이 토큰을 얼마나 썼고 실제 요금이 얼마인지</small>
        </Link>
      </div>
    </SectionGate>
  );
}

/** 모의계좌 한눈에 (읽기 전용) */
function PaperAccount() {
  const cur = useCurrency();
  const { data: sys } = useSystem();
  const { data: pf, isPending } = usePortfolio();
  const live = sys?.trading_mode === "live";
  return (
    <section className="card">
      <div className="card-head">
        <h2>{live ? "💳 실거래 계좌" : "🧾 모의계좌"}</h2>
        <span className={`pill ${live ? "sell" : ""}`}>{live ? "실거래 LIVE" : "모의투자 PAPER"}</span>
      </div>
      {isPending ? (
        <SkeletonLines lines={3} label="계좌 불러오는 중" />
      ) : pf ? (
        <dl className="kv-grid">
          <div>
            <dt>시작 금액</dt>
            <dd className="num">{money(pf.initial_cash, cur)}</dd>
          </div>
          <div>
            <dt>총자산</dt>
            <dd className="num">{money(pf.equity, cur)}</dd>
          </div>
          <div>
            <dt>누적 수익률</dt>
            <dd className={`num ${signClass(pf.total_return_pct)}`}>{fmtPct(pf.total_return_pct)}</dd>
          </div>
          <div>
            <dt>현금 · 주문 가능</dt>
            <dd className="num">
              {money(pf.cash, cur)} · {money(pf.available_cash, cur)}
            </dd>
          </div>
          <div>
            <dt>낸 수수료 · 세금</dt>
            <dd className="num">
              {money(pf.fees_paid, cur)} · {money(pf.taxes_paid, cur)}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="muted">계좌를 불러오지 못했어요.</p>
      )}
      <p className="muted settings-foot">
        {live
          ? "실제 주문이 나가는 계좌예요. 리스크 한도와 킬 스위치를 꼭 확인하세요."
          : "실제 돈이 오가지 않는 연습 계좌예요. 체결·수수료·세금은 실제 규칙대로 계산해요."}{" "}
        <Link to="/portfolio">포트폴리오·주문 보기 →</Link>
      </p>
    </section>
  );
}

const SOURCE_KO: Record<string, string> = { toss: "토스증권 실시세", mock: "가상 시장", MOCK: "가상 데이터" };

/** 데이터 연결 상태: 시세·실시간 이벤트·재무·뉴스·LLM */
function DataStatus() {
  const api = useApi();
  const { data: sys } = useSystem();
  const live = useConnected(api.market);
  if (!sys) return null;
  const rows: { label: string; ok: boolean | null; value: string }[] = [
    {
      label: "토스 시세",
      ok: sys.toss_ok,
      value: sys.toss_ok ? "연결됨" : (sys.toss_issue ?? "연결 안 됨"),
    },
    { label: "시세 원천", ok: null, value: SOURCE_KO[sys.data_source] ?? sys.data_source },
    { label: "실시간 이벤트", ok: live, value: live ? "연결됨" : "다시 연결 중…" },
    { label: "재무", ok: null, value: SOURCE_KO[sys.research.financials] ?? sys.research.financials },
    { label: "뉴스", ok: null, value: SOURCE_KO[sys.research.news] ?? sys.research.news },
    { label: "LLM", ok: sys.llm.configured, value: llmChipText(sys.llm) },
  ];
  return (
    <section className="card">
      <h2>데이터 연결 상태</h2>
      <ul className="status-list" aria-label="데이터 연결 상태">
        {rows.map((r) => (
          <li key={r.label}>
            <span className={`status-dot ${r.ok == null ? "" : r.ok ? "on" : "off"}`} aria-hidden />
            <span className="status-label">{r.label}</span>
            <span className={`status-value ${r.ok === false ? "warn" : ""}`}>{r.value}</span>
          </li>
        ))}
      </ul>
      {!sys.llm.configured && (
        <p className="muted settings-foot">
          LLM 이 없으면 규칙 전략으로 판단해요. <Link to="/settings/llm">LLM 설정 →</Link>
        </p>
      )}
    </section>
  );
}

/** 관심 종목 — 휴대폰에서 줄을 누르면 종목 빠른 보기 시트, 넓은 화면은 종목 화면으로 */
function Watchlist() {
  const qc = useQueryClient();
  const api = useApi();
  const q = useQuery(watchlistQuery(api));
  const [sym, setSym] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: api.key("watchlist") });
  const add = useMutation({
    mutationFn: (symbol: string) => api.post("/api/watchlist", { symbol }),
    onSuccess: (_d, symbol) => {
      setSym("");
      void refresh();
      toast(`⭐ ${symbol} 을(를) 관심 종목에 넣었어요`);
    },
  });
  const remove = useMutation({
    mutationFn: (s: string) => api.del(`/api/watchlist/${s}`),
    onSuccess: (_d, s) => {
      void refresh();
      toast(`${s} 을(를) 관심 종목에서 뺐어요`);
    },
    onError: (e) => toast.error(`관심 종목에서 빼지 못했어요: ${errorText(e)}`),
  });
  const quick = (e: MouseEvent, w: WatchItem, market: MarketCode) => {
    if (!isMobile()) return;
    e.preventDefault();
    openStockSheet({ symbol: w.symbol, name: w.name, market });
  };
  const list = q.data ?? [];
  return (
    <section className="card">
      <div className="card-head">
        <h2>관심 종목</h2>
        <small className="muted">
          {MARKET_META[api.market].flag} {list.length}개
        </small>
      </div>
      <p className="muted">매니저가 매 세션 포트폴리오와 함께 참고하는 목록이에요.</p>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (sym.trim()) add.mutate(sym.trim());
        }}
      >
        <input
          value={sym}
          onChange={(e) => setSym(e.target.value)}
          placeholder={api.market === "US" ? "티커 (예: AAPL)" : "종목코드 (예: 005930 / MOCK01)"}
          aria-label="관심 종목 코드"
        />
        <button className="btn tiny" disabled={add.isPending}>
          추가
        </button>
      </form>
      {add.isError && <p className="error">{errorText(add.error)}</p>}
      {list.length === 0 ? (
        <div className="empty-action">
          <p className="muted">아직 관심 종목이 없어요.</p>
          <button type="button" className="btn tiny ghost" onClick={openPalette}>
            🔍 종목 찾기
          </button>
        </div>
      ) : (
        <ul className="watch">
          {list.map((w) => (
            <li key={w.symbol}>
              <Link to={`/stock/${w.symbol}`} onClick={(e) => quick(e, w, api.market)} className="press">
                {w.name || w.symbol} <small className="muted">{w.symbol}</small>
              </Link>
              <button
                className="link"
                onClick={() => remove.mutate(w.symbol)}
                disabled={remove.isPending}
                aria-label={`${w.name || w.symbol} 관심 종목에서 빼기`}
              >
                삭제
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
