import { useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../api/client";
import type {
  MemoCard,
  MemoCardCreate,
  MemoCardPatch,
  MemoCards,
  MemoKind,
  MemoryTopic,
  MemoryTopics,
  MemoSettings,
  StockSearchItem,
  TopicPin,
} from "../api/types";
import { Loading } from "../components/Loading";
import { Modal } from "../components/Modal";
import { invalidateNames, useMarket } from "../market";
import { toast } from "../ui/toast";
import { useDebounced } from "../ui/useDebounced";
import {
  EXPIRY_PRESETS,
  expiryFor,
  fmtDay,
  groupCards,
  isExpiredOn,
  isLive,
  LLM_LEVEL_LABEL,
  looksLikeCode,
  memoApi,
  memoPrefix,
  MEMO_KINDS,
  memoKind,
  presetOf,
  shortDay,
  stockOptionText,
  todayKst,
  topicPercent,
  topicState,
} from "../utils/memo";
import "./memoTopics.css";

/** 검색어를 다 친 뒤 이만큼 멈추면 찾는다(ms) */
const SEARCH_DELAY = 250;

/** 카드가 바뀌면 같이 다시 받을 화면: 매니저의 기억(에이전트 구조) · 관심 종목(시세 갱신 목록) */
const RELATED_KEYS = ["agent-structure", "watchlist"];

/** 지금 시장의 메모 API(국장 /api/memory… · 미장 /api/us/memory…)와 캐시 키 — 시장마다 카드가 따로다 */
function useMemoScope() {
  const { market } = useMarket();
  return useMemo(
    () => ({ market, api: memoApi(memoPrefix(market)), cardsKey: [market, "memory-cards"] }),
    [market],
  );
}

/**
 * 📌 메모 카드 — 매니저에게 남기는 기억을 카드로 직접 관리한다.
 * 켜져 있고 만료되지 않은 카드만 판단·답장 때 기억으로 들어간다.
 */
export function MemoPage() {
  const m = useMemoScope();
  const q = useQuery({ queryKey: m.cardsKey, queryFn: m.api.list });
  const act = useCardActions();
  if (q.isError) return <p className="error">메모 카드를 불러오지 못했어요: {errorText(q.error)}</p>;
  if (!q.data) return <Loading />;

  const { cards, settings } = q.data;
  // 만료 기준 날짜는 서버가 준 시장 날짜(미장은 뉴욕 날짜) — 예전 서버면 한국 날짜
  const today = q.data.today ?? todayKst();
  const groups = groupCards(cards);
  const liveCount = cards.filter(isLive).length;

  return (
    <div className="stack memo-page">
      <section className="card memo-intro" aria-labelledby="memo-title">
        <div className="card-head">
          <h2 id="memo-title">📌 메모 카드</h2>
          <span className="pill memo-live" data-testid="memo-live-count">
            켜진 카드 {liveCount}개
          </span>
        </div>
        <p className="muted memo-lead">
          매니저는 판단·답장 때마다 켜져 있는 카드만 기억으로 받아요. 끄거나 만료되면 빠져요.
        </p>
        <AddCard today={today} />
      </section>

      {act.error && (
        <p className="error memo-error" role="alert">
          {act.error}{" "}
          <button type="button" className="link" onClick={act.clearError}>
            닫기
          </button>
        </p>
      )}

      <div className="memo-groups">
        {MEMO_KINDS.map((k) => {
          const list = groups[k.key];
          const on = list.filter(isLive).length;
          return (
            <section key={k.key} className={`card memo-group k-${k.key}`} aria-label={k.label}>
              <div className="memo-group-head">
                <h3>
                  <span aria-hidden>{k.icon}</span> {k.label}
                </h3>
                <span className="muted memo-count" data-testid={`memo-count-${k.key}`}>
                  {list.length}개{list.length > 0 && on !== list.length ? ` · 켜짐 ${on}` : ""}
                </span>
              </div>
              <small className="muted memo-hint">{k.hint}</small>
              {list.length === 0 ? (
                <p className="muted memo-empty">아직 카드가 없어요.</p>
              ) : (
                <ul className="memo-list">
                  {list.map((c) => (
                    <MemoCardItem
                      key={c.id}
                      card={c}
                      today={today}
                      onPatch={(patch) => act.update.mutate({ id: c.id, patch })}
                      onDelete={() => act.remove.mutate(c.id)}
                    />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      <TopicMemoryCard settings={settings} />
    </div>
  );
}

/** 카드 고치기·끄기·지우기 — 화면을 먼저 바꾸고, 실패하면 그 카드만 되돌린 뒤 서버 오류를 보여 준다 */
function useCardActions() {
  const qc = useQueryClient();
  const m = useMemoScope();
  const [error, setError] = useState<string | null>(null);
  const setCards = (fn: (cards: MemoCard[]) => MemoCard[]) =>
    qc.setQueryData<MemoCards>(m.cardsKey, (old) => old && { ...old, cards: fn(old.cards) });
  const related = () => {
    invalidateNames(qc, RELATED_KEYS, m.market);
  };
  const begin = async () => {
    setError(null);
    await qc.cancelQueries({ queryKey: m.cardsKey });
    return qc.getQueryData<MemoCards>(m.cardsKey)?.cards ?? [];
  };

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: MemoCardPatch }) => m.api.update(id, patch),
    onMutate: async ({ id, patch }) => {
      const prev = (await begin()).find((c) => c.id === id);
      const today = qc.getQueryData<MemoCards>(m.cardsKey)?.today ?? todayKst();
      setCards((cards) =>
        cards.map((c) =>
          c.id === id
            ? {
                ...c,
                ...patch,
                expired: "expires_on" in patch ? isExpiredOn(patch.expires_on ?? null, today) : c.expired,
              }
            : c,
        ),
      );
      return { prev };
    },
    onError: (e, _v, ctx) => {
      const prev = ctx?.prev;
      if (prev) setCards((cards) => cards.map((c) => (c.id === prev.id ? prev : c)));
      setError(errorText(e));
    },
    onSuccess: (card) => setCards((cards) => cards.map((c) => (c.id === card.id ? card : c))),
    onSettled: related,
  });

  const remove = useMutation({
    mutationFn: (id: number) => m.api.remove(id),
    onMutate: async (id) => {
      const cards = await begin();
      const index = cards.findIndex((c) => c.id === id);
      setCards((cs) => cs.filter((c) => c.id !== id));
      return { prev: index >= 0 ? cards[index] : undefined, index };
    },
    onError: (e, id, ctx) => {
      if (ctx?.prev) {
        const { prev, index } = ctx;
        setCards((cs) =>
          cs.some((c) => c.id === id) ? cs : [...cs.slice(0, index), prev, ...cs.slice(index)],
        );
      }
      setError(errorText(e));
    },
    onSuccess: () => toast("카드를 지웠어요"),
    onSettled: related,
  });

  return { update, remove, error, clearError: () => setError(null) };
}

// ---------- 카드 추가 ----------
function AddCard({ today }: { today: string }) {
  const qc = useQueryClient();
  const m = useMemoScope();
  const ids = useId();
  const [kind, setKind] = useState<MemoKind>("watch");
  const [stock, setStock] = useState<StockSearchItem | null>(null);
  const [query, setQuery] = useState("");
  const [text, setText] = useState("");
  const [expires, setExpires] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const info = memoKind(kind);

  const create = useMutation({
    mutationFn: (body: MemoCardCreate) => m.api.create(body),
    onSuccess: (card) => {
      qc.setQueryData<MemoCards>(
        m.cardsKey,
        (old) => old && { ...old, cards: [card, ...old.cards.filter((c) => c.id !== card.id)] },
      );
      invalidateNames(qc, RELATED_KEYS, m.market);
      toast("📌 추가했어요 ✓ 다음 판단·답장부터 기억에 들어가요.");
      setStock(null);
      setQuery("");
      setText("");
      setExpires(null);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (create.isPending) return;
    create.reset();
    const t = text.trim();
    const body: MemoCardCreate = { kind, expires_on: expires };
    if (info.stock) {
      const typed = query.trim().toUpperCase();
      const symbol = stock?.symbol ?? (looksLikeCode(typed, m.market) ? typed : null);
      if (!symbol)
        return setErr(
          m.market === "US"
            ? "종목을 검색해서 골라 주세요 (티커를 바로 적어도 돼요 · 예: AAPL)"
            : "종목을 검색해서 골라 주세요 (종목코드 6자리를 바로 적어도 돼요)",
        );
      body.symbol = symbol;
      if (t) body.text = t;
    } else {
      if (!t) return setErr("운용 지시 내용을 적어 주세요");
      body.text = t;
    }
    if (expires && expires < today) return setErr("만료일은 오늘이거나 그 뒤여야 해요");
    setErr(null);
    create.mutate(body);
  };

  return (
    <form className="memo-add" onSubmit={submit} aria-label="메모 카드 추가" noValidate>
      <div className="seg memo-kind-seg" role="group" aria-label="카드 종류">
        {MEMO_KINDS.map((k) => (
          <button
            key={k.key}
            type="button"
            className={kind === k.key ? `active k-${k.key}` : `k-${k.key}`}
            aria-pressed={kind === k.key}
            onClick={() => {
              setKind(k.key);
              setErr(null);
              create.reset();
            }}
          >
            <span aria-hidden>{k.icon}</span> {k.label}
          </button>
        ))}
      </div>
      <small className="muted memo-hint">{info.hint}</small>

      <div className="memo-add-grid">
        {info.stock ? (
          <>
            <StockPicker
              value={stock}
              query={query}
              onQuery={(v) => {
                setQuery(v);
                setErr(null);
              }}
              onPick={(s) => {
                setStock(s);
                setErr(null);
              }}
            />
            <label htmlFor={`${ids}-text`}>
              메모 (선택)
              <textarea
                id={`${ids}-text`}
                rows={2}
                maxLength={500}
                placeholder="예: 실적 발표 전까지 지켜보기"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
          </>
        ) : (
          <label htmlFor={`${ids}-text`} className="memo-add-note">
            운용 지시
            <textarea
              id={`${ids}-text`}
              rows={3}
              maxLength={500}
              required
              aria-required="true"
              placeholder="예: 이번 주는 현금 비중을 30% 이상 유지해 줘"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setErr(null);
              }}
            />
          </label>
        )}
      </div>

      <div className="memo-add-foot">
        <ExpiryPicker label="만료일 (선택)" value={expires} today={today} onChange={setExpires} />
        <button type="submit" className="btn primary memo-add-btn" disabled={create.isPending}>
          {create.isPending ? "추가하는 중…" : "카드 추가"}
        </button>
      </div>
      <div aria-live="polite" className="memo-status">
        {err && <p className="error">{err}</p>}
        {create.isError && <p className="error">{errorText(create.error)}</p>}
      </div>
    </form>
  );
}

/** 종목 검색(코드 앞자리·이름 일부) → 목록에서 고르기. ↑/↓ 로 옮기고 Enter 로 고른다 */
function StockPicker({
  value,
  query,
  onQuery,
  onPick,
}: {
  value: StockSearchItem | null;
  query: string;
  onQuery: (v: string) => void;
  onPick: (s: StockSearchItem | null) => void;
}) {
  const ids = useId();
  const listId = `${ids}-list`;
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const term = useDebounced(query.trim(), SEARCH_DELAY);
  const m = useMemoScope();
  const search = useQuery({
    queryKey: [m.market, "market-search", term],
    queryFn: () => m.api.search(term),
    enabled: term.length > 0,
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  if (value) {
    return (
      <div className="memo-picked">
        <span className="memo-picked-label">종목</span>
        <div className="memo-picked-row">
          <span className="chip memo-picked-chip" data-testid="memo-picked">
            {stockOptionText(value)}
          </span>
          <button
            type="button"
            className="btn tiny ghost"
            onClick={() => {
              onPick(null);
              window.setTimeout(() => input.current?.focus(), 0);
            }}
          >
            다시 고르기
          </button>
        </div>
      </div>
    );
  }

  const items = term && query.trim() ? (search.data ?? []) : [];
  const active = items.length ? Math.min(hi, items.length - 1) : -1;
  const show = open && query.trim().length > 0 && term.length > 0;
  const pick = (s: StockSearchItem) => {
    onPick(s);
    onQuery("");
    setOpen(false);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // 한글 조합 중(IME)의 Enter·화살표는 글자를 확정하는 키라 목록을 건드리지 않는다
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHi(Math.min(active + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi(Math.max(active - 1, 0));
    } else if (e.key === "Enter") {
      if (show && active >= 0) {
        e.preventDefault();
        pick(items[active]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="combo">
      <label htmlFor={`${ids}-input`}>종목</label>
      <input
        ref={input}
        id={`${ids}-input`}
        type="search"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={show}
        aria-controls={listId}
        aria-activedescendant={show && active >= 0 ? `${ids}-opt-${active}` : undefined}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        placeholder={
          m.market === "US" ? "종목명 또는 티커 (예: 애플, AAPL)" : "종목명 또는 코드 (예: 삼성전자, 005930)"
        }
        value={query}
        onChange={(e) => {
          onQuery(e.target.value);
          setHi(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKey}
      />
      {show && (
        <div className="combo-pop">
          {items.length > 0 ? (
            <ul className="combo-list" id={listId} role="listbox" aria-label="검색 결과">
              {items.map((s, i) => (
                <li
                  key={s.symbol}
                  id={`${ids}-opt-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={i === active ? "combo-opt hi" : "combo-opt"}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pick(s)}
                >
                  <b>{s.name}</b> <span className="muted">· {s.symbol}</span>
                  {s.market && <span className="muted"> · {s.market}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="combo-empty muted" role="status">
              {search.isError
                ? `종목 검색에 실패했어요: ${errorText(search.error)}`
                : search.isFetching
                  ? "찾는 중…"
                  : "검색 결과가 없어요"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** 만료일: 빠른 버튼(오늘까지 / 1주 / 1달 / 없음) + 날짜 직접 고르기 */
function ExpiryPicker({
  label,
  value,
  today,
  onChange,
}: {
  label: string;
  value: string | null;
  today: string;
  onChange: (v: string | null) => void;
}) {
  const preset = presetOf(value, today);
  return (
    <div className="memo-expiry" role="group" aria-label={label}>
      <span className="memo-expiry-label">{label}</span>
      <div className="memo-expiry-row">
        <div className="seg memo-expiry-seg">
          {EXPIRY_PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              className={preset === p.key ? "active" : ""}
              aria-pressed={preset === p.key}
              onClick={() => onChange(expiryFor(p.key, today))}
            >
              {p.label}
            </button>
          ))}
        </div>
        <input
          type="date"
          className="memo-date"
          aria-label={`${label} 날짜`}
          min={today}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value || null)}
        />
      </div>
    </div>
  );
}

// ---------- 카드 한 장 ----------
function cardTitle(c: MemoCard): string {
  if (c.kind !== "note") return c.name || c.symbol || "종목";
  const t = c.text.trim().replace(/\s+/g, " ");
  return t.length > 16 ? `운용 지시 '${t.slice(0, 16)}…'` : `운용 지시 '${t}'`;
}

function MemoCardItem({
  card,
  today,
  onPatch,
  onDelete,
}: {
  card: MemoCard;
  today: string;
  onPatch: (patch: MemoCardPatch) => void;
  onDelete: () => void;
}) {
  const ids = useId();
  const info = memoKind(card.kind);
  const live = isLive(card);
  const title = cardTitle(card);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ kind: card.kind, text: card.text, expires_on: card.expires_on });
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  const startEdit = () => {
    setDraft({ kind: card.kind, text: card.text, expires_on: card.expires_on });
    setErr(null);
    setEditing(true);
  };
  const save = () => {
    const text = draft.text.trim();
    if (card.kind === "note" && !text) return setErr("운용 지시는 비울 수 없어요");
    const patch: MemoCardPatch = {};
    if (text !== card.text) patch.text = text;
    if (draft.kind !== card.kind) patch.kind = draft.kind;
    if (draft.expires_on !== card.expires_on) {
      if (draft.expires_on && draft.expires_on < today) return setErr("만료일은 오늘이거나 그 뒤여야 해요");
      patch.expires_on = draft.expires_on;
    }
    setEditing(false);
    if (Object.keys(patch).length > 0) onPatch(patch);
  };

  const cls = ["memo-card", `k-${card.kind}`, live ? "" : "dim", card.expired ? "expired" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <li className={cls} aria-label={`${info.label} · ${title}`}>
      <div className="memo-card-head">
        <span className={`memo-kind k-${card.kind}`}>
          <span aria-hidden>{info.icon}</span> {info.label}
        </span>
        <label
          className="memo-switch"
          title={card.active ? "켜짐 — 기억에 들어가요" : "꺼짐 — 기억에서 빠져요"}
        >
          <span className="memo-switch-text">{card.active ? "켜짐" : "꺼짐"}</span>
          <input
            type="checkbox"
            role="switch"
            className="switch"
            checked={card.active}
            aria-label={`${title} 켜기`}
            onChange={(e) => onPatch({ active: e.target.checked })}
          />
        </label>
      </div>

      {card.symbol && (
        <Link to={`/stock/${card.symbol}`} className="memo-stock">
          <b>{card.name || card.symbol}</b> <small className="muted">{card.symbol}</small>
        </Link>
      )}

      {editing ? (
        <div
          className="memo-edit"
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
          }}
        >
          {info.stock && (
            <label htmlFor={`${ids}-kind`}>
              종류
              <select
                id={`${ids}-kind`}
                value={draft.kind}
                onChange={(e) => setDraft({ ...draft, kind: e.target.value as MemoKind })}
              >
                {MEMO_KINDS.filter((k) => k.stock).map((k) => (
                  <option key={k.key} value={k.key}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label htmlFor={`${ids}-text`}>
            {card.kind === "note" ? "운용 지시" : "메모"}
            <textarea
              id={`${ids}-text`}
              rows={3}
              maxLength={500}
              autoFocus
              value={draft.text}
              onChange={(e) => setDraft({ ...draft, text: e.target.value })}
            />
          </label>
          <ExpiryPicker
            label="만료일"
            value={draft.expires_on}
            today={today}
            onChange={(v) => setDraft({ ...draft, expires_on: v })}
          />
          {err && (
            <p className="error" role="alert">
              {err}
            </p>
          )}
          <div className="btn-row">
            <button type="button" className="btn tiny primary" onClick={save}>
              저장
            </button>
            <button type="button" className="btn tiny ghost" onClick={() => setEditing(false)}>
              취소
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className={card.text ? "memo-text" : "memo-text empty"}
          title="눌러서 고치기"
          aria-label={`${title} 메모: ${card.text || "없음"} (눌러서 고치기)`}
          onClick={startEdit}
        >
          {card.text || "메모 없음 — 눌러서 적기"}
        </button>
      )}

      <div className="memo-card-foot">
        <span className={card.expired ? "memo-exp expired" : "memo-exp"}>
          {card.expires_on
            ? card.expired
              ? `${fmtDay(card.expires_on)} 만료됨`
              : `${fmtDay(card.expires_on)}까지`
            : "만료 없음"}
        </span>
        <span className="memo-actions">
          {!editing && (
            <button
              type="button"
              className="btn tiny ghost"
              aria-label={`${title} 고치기`}
              onClick={startEdit}
            >
              고치기
            </button>
          )}
          <button
            type="button"
            className="btn tiny ghost memo-del"
            aria-label={`${title} 삭제`}
            onClick={() => setConfirm(true)}
          >
            삭제
          </button>
        </span>
      </div>

      {confirm && (
        <Modal
          title="카드를 지울까요?"
          tone="danger"
          onClose={() => setConfirm(false)}
          actions={
            <>
              <button className="btn ghost" onClick={() => setConfirm(false)}>
                취소
              </button>
              <button
                className="btn primary"
                onClick={() => {
                  setConfirm(false);
                  onDelete();
                }}
              >
                지우기
              </button>
            </>
          }
        >
          <p>
            <b>{title}</b> 카드를 지워요. 잠깐 빼 두기만 하려면 지우지 말고 꺼 두세요.
          </p>
        </Modal>
      )}
    </li>
  );
}

// ---------- 대화에서 기억하기 (D-67) ----------
const PIN_OPTIONS: { value: TopicPin; label: string; hint: string }[] = [
  { value: "pin", label: "늘 기억", hint: "중요도와 관계없이 늘 기억에 넣어요" },
  { value: "", label: "자동", hint: "되풀이한 날·매니저 판단으로 정해요" },
  { value: "mute", label: "빼기", hint: "기억에 넣지 않아요" },
];

function TopicMemoryCard({ settings }: { settings: MemoSettings }) {
  const qc = useQueryClient();
  const m = useMemoScope();
  const topicsKey = useMemo(() => [m.market, "memory-topics"], [m.market]);
  const q = useQuery({ queryKey: topicsKey, queryFn: m.api.topics });
  const related = () => invalidateNames(qc, ["agent-structure"], m.market);
  const setTopics = (fn: (list: MemoryTopic[]) => MemoryTopic[]) =>
    qc.setQueryData<MemoryTopics>(topicsKey, (old) => old && { ...old, topics: fn(old.topics) });

  const save = useMutation({
    mutationFn: (on: boolean) => m.api.saveSettings({ topics: on }),
    onMutate: async (on) => {
      await qc.cancelQueries({ queryKey: m.cardsKey });
      const prev = qc.getQueryData<MemoCards>(m.cardsKey)?.settings;
      qc.setQueryData<MemoCards>(
        m.cardsKey,
        (old) => old && { ...old, settings: { ...old.settings, topics: on } },
      );
      return { prev };
    },
    onError: (_e, _on, ctx) => {
      const prev = ctx?.prev;
      if (prev) qc.setQueryData<MemoCards>(m.cardsKey, (old) => old && { ...old, settings: prev });
    },
    onSuccess: (st) => {
      qc.setQueryData<MemoCards>(
        m.cardsKey,
        (old) => old && { ...old, settings: { ...old.settings, ...st } },
      );
      qc.setQueryData<MemoryTopics>(topicsKey, (old) => old && { ...old, settings: st });
      toast(
        st.topics ? "대화에서 되풀이한 주제를 기억해요 ✓" : "대화 기억을 껐어요 — 메모 카드만 기억해요 ✓",
      );
    },
    onSettled: related,
  });
  const pin = useMutation({
    mutationFn: ({ id, value }: { id: number; value: TopicPin }) => m.api.pinTopic(id, value),
    onSuccess: (t) => setTopics((list) => list.map((x) => (x.id === t.id ? t : x))),
    onSettled: related,
  });
  const remove = useMutation({
    mutationFn: (id: number) => m.api.removeTopic(id),
    onSuccess: (_r, id) => setTopics((list) => list.filter((x) => x.id !== id)),
    onSettled: related,
  });
  const review = useMutation({
    mutationFn: m.api.reviewTopics,
    onSuccess: (d) => {
      qc.setQueryData<MemoryTopics>(topicsKey, d);
      const r = d.result;
      toast(r?.skipped ? r.skipped : `메시지 ${r?.messages ?? 0}개를 정리했어요 ✓`);
    },
    onSettled: related,
  });

  const data = q.data;
  const on = settings.topics;
  const list = data?.topics ?? [];
  const inCount = list.filter((t) => t.included).length;
  const threshold = Math.round((settings.threshold ?? 0.35) * 100);
  const err = save.error ?? pin.error ?? remove.error ?? review.error;
  return (
    <section className="card memo-settings topic-memory" aria-labelledby="memo-settings-title">
      <div className="card-head">
        <h3 id="memo-settings-title">🧠 대화에서 기억하기</h3>
        <label className="toggle memo-settings-toggle">
          <input
            type="checkbox"
            role="switch"
            className="switch"
            checked={on}
            aria-label="대화에서 기억하기"
            onChange={(e) => {
              save.reset();
              save.mutate(e.target.checked);
            }}
          />
          <span>{on ? "켜짐" : "꺼짐"}</span>
        </label>
      </div>
      <p className="muted memo-settings-hint">
        트레이딩 룸에서 꺼낸 주제(종목·업종·테마·지시)를 주제별로 모아요. <b>여러 날 되풀이해 물으면</b>{" "}
        중요도가 오르고 오래 기억하고, <b>한 번 묻고 만 주제</b>는 그날만 들어가고 며칠 안에 빠져요. 새
        메시지가 있으면 판단 세션 전에 매니저가 주제마다 올리거나 내려요(LLM 1번). &apos;잊어줘&apos;라고 하면
        다시 꺼낼 때까지 빼요.
      </p>
      <div className="topic-bar">
        <span className="muted" data-testid="topic-in-count">
          지금 기억에 들어가는 주제 {inCount}개 · 중요도 {threshold} 이상, 최대 {settings.max_in_prompt ?? 8}
          개
        </span>
        <button
          type="button"
          className="btn tiny"
          disabled={!on || !data?.llm || review.isPending}
          title={data && !data.llm ? "LLM 이 설정되지 않아 정리할 수 없어요" : "LLM 을 한 번 불러요"}
          onClick={() => review.mutate()}
        >
          {review.isPending ? "정리 중…" : "지금 정리"}
        </button>
      </div>
      {q.isError ? (
        <p className="error">주제를 불러오지 못했어요: {errorText(q.error)}</p>
      ) : !data ? (
        <p className="muted">불러오는 중…</p>
      ) : list.length === 0 ? (
        <p className="muted memo-empty">
          아직 정리된 주제가 없어요. 트레이딩 룸에서 이야기하면 다음 판단 세션 전에 정리돼요.
        </p>
      ) : (
        <ul className="topic-list" aria-label="대화에서 모은 주제">
          {list.map((t) => (
            <TopicItem
              key={t.id}
              topic={t}
              busy={pin.isPending || remove.isPending}
              onPin={(value) => pin.mutate({ id: t.id, value })}
              onRemove={() => remove.mutate(t.id)}
            />
          ))}
        </ul>
      )}
      <p className="memo-settings-hint">
        <Link to="/agent">에이전트 구조에서 매니저의 기억 보기 →</Link>
      </p>
      <div aria-live="polite">{err && <p className="error">{errorText(err)}</p>}</div>
    </section>
  );
}

function TopicItem({
  topic: t,
  busy,
  onPin,
  onRemove,
}: {
  topic: MemoryTopic;
  busy: boolean;
  onPin: (v: TopicPin) => void;
  onRemove: () => void;
}) {
  const st = topicState(t);
  const pct = topicPercent(t.score);
  const quote = t.quotes.length ? t.quotes[t.quotes.length - 1].text : "";
  return (
    <li className={`topic-item ${st.tone}`} data-testid={`topic-${t.key}`}>
      <div className="topic-head">
        <b className="topic-label">{t.label}</b>
        <span className={`pill topic-state ${st.tone}`}>{st.label}</span>
        <span className="topic-score" aria-label={`중요도 ${pct}`}>
          <span className="topic-meter" aria-hidden>
            <i style={{ width: `${pct}%` }} />
          </span>
          <small className="num">{pct}</small>
        </span>
      </div>
      <small className="muted topic-meta">
        {t.days}일 언급{t.streak > 1 ? ` · 연달아 ${t.streak}일` : ""} · 마지막 {shortDay(t.last_day)} ·
        메시지 {t.mentions}개
      </small>
      {t.llm_reason && (
        <small className="topic-reason">
          매니저 {LLM_LEVEL_LABEL[t.llm_level] ?? "유지"}
          {t.forgotten ? " · 잊음" : ""} — {t.llm_reason}
        </small>
      )}
      {quote && <q className="topic-quote">{quote}</q>}
      <div className="topic-actions">
        <div className="seg" role="group" aria-label={`${t.label} 기억 방식`}>
          {PIN_OPTIONS.map((o) => (
            <button
              key={o.value || "auto"}
              type="button"
              className={t.pin === o.value ? "active" : ""}
              aria-pressed={t.pin === o.value}
              title={o.hint}
              disabled={busy}
              onClick={() => onPin(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <button type="button" className="btn tiny ghost memo-del" disabled={busy} onClick={onRemove}>
          지우기
        </button>
      </div>
    </li>
  );
}
