import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../api/client";
import type { MarketView, Portfolio, StockSearchItem } from "../api/types";
import { isAllRoute, MARKET_META, useApi, useMarket } from "../market";
import { SECTION_META } from "../pages/settings/meta";
import { useDebounced } from "../ui/useDebounced";
import "./CommandPalette.css";

/** 검색어를 다 친 뒤 이만큼 멈추면 종목을 찾는다(ms) */
export const PALETTE_SEARCH_DELAY = 180;
/** 종목 검색 결과는 이만큼만 보여 준다 */
const MAX_STOCKS = 8;

type Group = "화면" | "설정" | "시장" | "보유 종목" | "종목 검색";

interface Item {
  id: string;
  group: Group;
  icon: string;
  label: string;
  hint?: string;
  /** 검색에 함께 쓰는 말 */
  keywords?: string;
  run: () => void;
}

const PAGES: { to: string; icon: string; label: string; keywords: string }[] = [
  {
    to: "/",
    icon: "🏠",
    label: "홈 (대시보드 · 한눈에 보기)",
    keywords: "home dashboard 대시보드 홈 한눈에 overview",
  },
  { to: "/room", icon: "💬", label: "트레이딩 룸", keywords: "room chat 채팅 대화 세션" },
  {
    to: "/news",
    icon: "📰",
    label: "뉴스 룸",
    keywords: "news 뉴스 세계 국제 경제 증시 이슈 헤드라인 속보",
  },
  { to: "/agent", icon: "🦉", label: "매니저 › 구조·기억", keywords: "agent 매니저 에이전트 구조 기억 도구" },
  { to: "/memo", icon: "📌", label: "매니저 › 메모 카드", keywords: "memo 매니저 메모 카드 기억" },
  { to: "/portfolio", icon: "📒", label: "포트폴리오·주문", keywords: "portfolio 자산 주문 체결 결정" },
  { to: "/backtest", icon: "🧪", label: "백테스트", keywords: "backtest 백테스트 전략" },
  { to: "/settings", icon: "⚙️", label: "설정", keywords: "settings 설정" },
  { to: "/logs", icon: "📜", label: "이벤트 로그", keywords: "logs 로그 감사 기록" },
  { to: "/costs", icon: "💸", label: "설정 › LLM 비용", keywords: "costs 비용 요금 토큰 llm" },
];

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, "");
const matchesQuery = (it: Item, q: string) => {
  if (!q) return true;
  const hay = norm(`${it.label} ${it.hint ?? ""} ${it.keywords ?? ""}`);
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
};

/**
 * ⌘K 빠른 이동 — 화면·설정 주제로 가기, 시장 바꾸기, 종목 찾기(지금 시장: 국장 /api/market/search · 미장 /api/us/…).
 * ↑/↓ 로 고르고 Enter, Esc 로 닫는다. 읽기 전용 이동만 한다 (주문·설정 변경은 하지 않는다)
 */
export function CommandPalette({ onClose }: { onClose: () => void }) {
  const ids = useId();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { view, market, available, setView } = useMarket();
  const api = useApi();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const term = useDebounced(q.trim(), PALETTE_SEARCH_DELAY);
  const search = useQuery({
    // 메모 카드의 종목 고르기와 같은 키·주소 — 같은 검색은 다시 받지 않는다
    queryKey: api.key("market-search", term),
    queryFn: () => api.get<StockSearchItem[]>(`/api/market/search?q=${encodeURIComponent(term)}&limit=20`),
    enabled: term.length > 0,
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  // 열릴 때 입력창으로, 닫히면 열기 전 자리로
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => before?.focus?.({ preventScroll: true });
  }, []);

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => {
      onClose();
      navigate(to);
    };
    const pick = (v: MarketView) => () => {
      onClose();
      // '전체'는 한눈에 보기 화면 — 시장 화면에서 고르면 그리로 간다 (시장 전환 버튼과 같게)
      setView(v);
      if (v === "ALL" && !isAllRoute(pathname)) navigate("/");
    };
    const out: Item[] = PAGES.map((p) => ({
      id: `page:${p.to}`,
      group: "화면" as const,
      icon: p.icon,
      label: p.label,
      keywords: p.keywords,
      run: go(p.to),
    }));
    for (const m of SECTION_META)
      out.push({
        id: `settings:${m.key}`,
        group: "설정",
        icon: m.icon,
        label: `설정 › ${m.title}`,
        hint: m.desc,
        keywords: `설정 settings ${m.keywords}`,
        run: go(`/settings/${m.key}`),
      });
    if (available.length > 1) {
      const views: MarketView[] = ["ALL", ...available];
      for (const v of views) {
        out.push({
          id: `market:${v}`,
          group: "시장",
          icon: v === "ALL" ? "🧭" : MARKET_META[v].flag,
          label: v === "ALL" ? "전체 보기 (두 시장 한눈에)" : `${MARKET_META[v].name}으로 보기`,
          hint: view === v ? "지금 보는 중" : undefined,
          keywords: `시장 market 전환 ${v === "ALL" ? "전체 all" : v === "KR" ? "국장 한국 kr" : "미장 미국 us"}`,
          run: pick(v),
        });
      }
    }
    // 보유 종목 (이미 받아 둔 계좌에서 — 요청을 더 하지 않는다)
    const held = qc.getQueryData<Portfolio>(api.key("portfolio"))?.positions ?? [];
    for (const p of held)
      out.push({
        id: `held:${p.symbol}`,
        group: "보유 종목",
        icon: "📈",
        label: p.name || p.symbol,
        hint: p.symbol,
        keywords: `${p.symbol} 보유`,
        run: go(`/stock/${p.symbol}`),
      });
    return out;
  }, [onClose, navigate, setView, pathname, available, view, qc, api]);

  const shown = useMemo<Item[]>(() => {
    const query = q.trim();
    const local = items.filter((it) => matchesQuery(it, query));
    // 검색어가 없으면 화면·시장·보유 종목 위주로 (설정 주제는 검색할 때)
    const base = query ? local : local.filter((it) => it.group !== "설정");
    if (!query || !term) return base;
    const heldIds = new Set(local.filter((it) => it.group === "보유 종목").map((it) => it.hint));
    const stocks: Item[] = (search.data ?? [])
      .filter((s) => !heldIds.has(s.symbol))
      .slice(0, MAX_STOCKS)
      .map((s) => ({
        id: `stock:${s.symbol}`,
        group: "종목 검색",
        icon: "🔎",
        label: s.name,
        hint: [s.symbol, s.market].filter(Boolean).join(" · "),
        run: () => {
          onClose();
          navigate(`/stock/${s.symbol}`);
        },
      }));
    return [...base, ...stocks];
  }, [items, q, term, search.data, onClose, navigate]);

  const active = shown.length ? Math.min(hi, shown.length - 1) : -1;

  // 고른 줄이 보이게
  useEffect(() => {
    const el = list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView?.({ block: "nearest" });
  }, [active]);

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // 한글 조합 중(IME)의 Enter·화살표는 글자를 확정하는 키라 목록을 건드리지 않는다
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi(shown.length ? (active + 1) % shown.length : 0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi(shown.length ? (active - 1 + shown.length) % shown.length : 0);
    } else if (e.key === "Home") {
      e.preventDefault();
      setHi(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setHi(Math.max(0, shown.length - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0) shown[active].run();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  const meta = MARKET_META[market];
  const searching = term.length > 0 && q.trim().length > 0;
  let status: string | null = null;
  if (searching && search.isError) status = `종목 검색에 실패했어요: ${errorText(search.error)}`;
  else if (searching && search.isFetching && !search.data) status = "종목 찾는 중…";
  else if (shown.length === 0) status = q.trim() ? "맞는 항목이 없어요" : null;

  let lastGroup: Group | null = null;
  return createPortal(
    <div className="palette-backdrop" onClick={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="빠른 이동"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="palette-input">
          <span aria-hidden>🔍</span>
          <input
            ref={input}
            type="search"
            role="combobox"
            aria-expanded="true"
            aria-controls={`${ids}-list`}
            aria-activedescendant={active >= 0 ? `${ids}-opt-${active}` : undefined}
            aria-autocomplete="list"
            aria-label="빠른 이동 · 종목 검색"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="go"
            placeholder={`화면·설정 이동, ${meta.flag} ${meta.name} 종목 검색 (이름·코드)`}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setHi(0);
            }}
            onKeyDown={onKey}
          />
          <button type="button" className="palette-close" onClick={onClose} aria-label="닫기">
            Esc
          </button>
        </div>
        <ul className="palette-list" id={`${ids}-list`} role="listbox" aria-label="이동할 곳" ref={list}>
          {shown.map((it, i) => {
            const head = it.group !== lastGroup;
            lastGroup = it.group;
            return (
              <li key={it.id} role="presentation">
                {head && (
                  <div className="palette-group" role="presentation">
                    {it.group === "종목 검색" ? `${meta.flag} ${meta.name} 종목` : it.group}
                  </div>
                )}
                <div
                  id={`${ids}-opt-${i}`}
                  role="option"
                  aria-selected={i === active}
                  data-index={i}
                  className={`palette-item ${i === active ? "hi" : ""}`}
                  onMouseMove={() => i !== active && setHi(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => it.run()}
                >
                  <span className="palette-icon" aria-hidden>
                    {it.icon}
                  </span>
                  <span className="palette-label">{it.label}</span>
                  {it.hint && <small className="palette-hint muted">{it.hint}</small>}
                </div>
              </li>
            );
          })}
        </ul>
        {status && (
          <p className="palette-status muted" role="status">
            {status}
          </p>
        )}
        <div className="palette-foot muted" aria-hidden>
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> 고르기
          </span>
          <span>
            <kbd>Enter</kbd> 열기
          </span>
          <span>
            <kbd>Esc</kbd> 닫기
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
