import { useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorText, get } from "../api/client";
import type { LlmCosts, LlmLedgerGroup, LlmOfficial, LlmOfficialProvider } from "../api/types";
import { Loading } from "../components/Loading";
import { useLlmCosts, useLlmOfficial } from "../hooks";
import {
  accountLabel,
  dayLong,
  daysOf,
  FILTER_KEYS,
  groupBy,
  isFiltered,
  kindLabel,
  marketLabel,
  monthLabel,
  monthLong,
  NO_FILTER,
  optionValues,
  profileLabel,
  PROVIDERS,
  providerLabel,
  safeUrl,
  SOURCE_KO,
  SOURCE_ORDER,
  strategyLabel,
  sum,
  tokenStack,
  type FilterKey,
  type Filters,
  type Totals,
} from "../utils/costs";
import { fmtIndicator, fmtKrwFromUsd, fmtNum, fmtTokens, fmtUsd } from "../utils/format";
import "./costs.css";

type Currency = "krw" | "usd";
type Metric = "cost" | "tokens";
type By = "model" | "account" | "kind" | "day";

/** LLM 요금 (D-66): 호출마다 어느 모델이 토큰을 얼마나 썼고 실제 요금이 얼마인지 — 무료/유료·제공사·모델·계좌로 나눠 본다 */
export function Costs() {
  const [month, setMonth] = useState<string | undefined>(undefined);
  const { data, isPending, isError, error, isPlaceholderData } = useLlmCosts(month);
  if (isPending) return <Loading cards={3} />;
  if (isError || !data) {
    return (
      <section className="card">
        <h2>💸 LLM 요금</h2>
        <p className="error">불러오지 못했어요: {errorText(error)}</p>
      </section>
    );
  }
  return <CostsView data={data} onMonth={setMonth} stale={isPlaceholderData} />;
}

function CostsView({
  data,
  onMonth,
  stale,
}: {
  data: LlmCosts;
  onMonth: (m: string) => void;
  stale: boolean;
}) {
  const [filters, setFilters] = useState<Filters>(NO_FILTER);
  const [cur, setCur] = useState<Currency>("krw");
  const [metric, setMetric] = useState<Metric>("cost");
  const [by, setBy] = useState<By>("model");
  const [dayIdx, setDayIdx] = useState<number | null>(null);

  const rate = data.usd_krw != null && data.usd_krw > 0 ? data.usd_krw : null;
  const currency: Currency = rate ? cur : "usd";
  const money = (v: number) => (currency === "krw" && rate ? fmtKrwFromUsd(v, rate) : fmtUsd(v));

  const monthGroups = useMemo(() => data.groups.filter((g) => g.month === data.month), [data]);
  const total = sum(monthGroups, filters);
  const today =
    data.month === data.today.slice(0, 7)
      ? sum(
          data.daily.filter((d) => d.day === data.today),
          filters,
        )
      : null;
  const empty = data.groups.length === 0;

  const label = (key: FilterKey, v: string) =>
    key === "profile"
      ? profileLabel(v)
      : key === "provider"
        ? providerLabel(v)
        : key === "market"
          ? marketLabel(v)
          : key === "strategy"
            ? strategyLabel(v, data.strategies)
            : key === "kind"
              ? kindLabel(v)
              : v || "모름";

  return (
    <div className={`stack costs-page${stale ? " costs-stale" : ""}`}>
      <section className="card costs-head">
        <div className="card-head">
          <h2>💸 LLM 요금</h2>
          <span className="muted">단가 확인일 {data.checked}</span>
        </div>
        <p className="costs-intro">
          {data.current_model ? (
            <>
              지금 쓰는 모델{" "}
              <span className={`pill${data.profile === "paid" ? " paid" : ""}`}>
                {profileLabel(data.profile)}
              </span>{" "}
              <b className="costs-model">{data.current_model}</b>
              {data.current_provider ? (
                <span className="muted"> · {providerLabel(data.current_provider)}</span>
              ) : null}
            </>
          ) : (
            <>LLM 이 설정돼 있지 않아요(규칙 기반).</>
          )}{" "}
          <span className="muted">호출마다 모델·토큰·요금을 장부에 남기고, 그 합계를 그대로 보여 줘요.</span>
        </p>
        <div className="costs-total" data-testid="costs-total">
          <div>
            <small className="muted">
              {monthLong(data.month)}
              {isFiltered(filters) ? " · 고른 조건" : ""}
            </small>
            <b className="costs-big">{money(total.cost)}</b>
            <small className="muted">
              호출 {fmtNum(total.calls)}회 · 입력 {fmtTokens(total.input)} · 출력 {fmtTokens(total.output)}{" "}
              토큰
            </small>
          </div>
          {today && (
            <div>
              <small className="muted">오늘</small>
              <b className="costs-mid">{money(today.cost)}</b>
              <small className="muted">호출 {fmtNum(today.calls)}회</small>
            </div>
          )}
        </div>
        <SourceChips t={total} />
        <div className="costs-toggles">
          {rate ? (
            <>
              <Seg
                label="통화"
                value={currency}
                onChange={setCur}
                options={[
                  { value: "krw", label: "원" },
                  { value: "usd", label: "$" },
                ]}
              />
              <span className="muted costs-fx">$1 = {fmtIndicator(rate, "원")} (토스)</span>
            </>
          ) : (
            <span className="muted costs-fx">환율을 받지 못해 $ 로만 보여요</span>
          )}
        </div>
      </section>

      {empty ? (
        <section className="card">
          <p className="muted empty" data-testid="costs-empty">
            아직 기록된 LLM 사용량이 없어요.
          </p>
        </section>
      ) : (
        <>
          <section className="card costs-filters" aria-label="거르기">
            <div className="card-head">
              <h3>거르기</h3>
              {isFiltered(filters) && (
                <button type="button" className="btn ghost" onClick={() => setFilters(NO_FILTER)}>
                  모두 보기
                </button>
              )}
            </div>
            <div className="costs-filter-grid">
              {FILTER_KEYS.map((k) => (
                <FilterSelect
                  key={k}
                  name={FILTER_NAMES[k]}
                  value={filters[k]}
                  values={optionValues(data.groups, k, filters)}
                  label={(v) => label(k, v)}
                  onChange={(v) => {
                    setFilters({ ...filters, [k]: v });
                    setDayIdx(null);
                  }}
                />
              ))}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>월별</h2>
              <Seg
                label="막대"
                value={metric}
                onChange={setMetric}
                options={[
                  { value: "cost", label: "요금" },
                  { value: "tokens", label: "토큰" },
                ]}
              />
            </div>
            <Bars
              keys={data.months}
              totals={groupBy(data.groups, (g) => g.month, filters)}
              sel={data.months.indexOf(data.month)}
              onSelect={(i) => {
                onMonth(data.months[i]);
                setDayIdx(null);
              }}
              colLabel={monthLabel}
              title={monthLong}
              metric={metric}
              money={money}
              testId="month-chart"
            />
          </section>

          <section className="card">
            <div className="card-head">
              <h2>일별 · {monthLong(data.month)}</h2>
            </div>
            <DayBars
              data={data}
              filters={filters}
              metric={metric}
              money={money}
              sel={dayIdx}
              onSelect={setDayIdx}
            />
          </section>

          <section className="card">
            <div className="card-head">
              <h2>{monthLong(data.month)} 나눠 보기</h2>
              <Seg
                label="묶어 보기"
                value={by}
                onChange={setBy}
                options={[
                  { value: "model", label: "모델" },
                  { value: "account", label: "계좌" },
                  { value: "kind", label: "종류" },
                  { value: "day", label: "날짜" },
                ]}
              />
            </div>
            <Breakdown data={data} groups={monthGroups} filters={filters} by={by} money={money} />
          </section>
        </>
      )}

      <Official data={data} money={money} />
      <Footnotes data={data} rate={rate} />
    </div>
  );
}

const FILTER_NAMES: Record<FilterKey, string> = {
  profile: "무료/유료",
  provider: "제공사",
  model: "모델",
  market: "시장",
  strategy: "계좌",
  kind: "종류",
};

/** 거르기 한 칸. 빈 값("" — 공용·주 계좌)도 고를 수 있게 값 앞에 표시를 붙여 둔다 */
function FilterSelect({
  name,
  value,
  values,
  label,
  onChange,
}: {
  name: string;
  value: string | null;
  values: string[];
  label: (v: string) => string;
  onChange: (v: string | null) => void;
}) {
  return (
    <label className="costs-filter">
      <span className="muted">{name}</span>
      <select
        aria-label={name}
        value={value == null ? "*" : `v:${value}`}
        onChange={(e) => onChange(e.target.value === "*" ? null : e.target.value.slice(2))}
      >
        <option value="*">전체</option>
        {values.map((v) => (
          <option key={v} value={`v:${v}`}>
            {label(v)}
          </option>
        ))}
      </select>
    </label>
  );
}

function SourceChips({ t }: { t: Totals }) {
  const items = SOURCE_ORDER.filter((s) => (t.by_source[s] ?? 0) > 0);
  if (items.length === 0) return null;
  return (
    <p className="costs-sources" aria-label="요금 출처">
      {items.map((s) => (
        <span key={s} className={`chip src-${s}`} title={SOURCE_HINT[s]}>
          {SOURCE_KO[s]} {fmtNum(t.by_source[s] ?? 0)}회
        </span>
      ))}
      {t.unpriced > 0 && <span className="muted">— 단가 모름 호출은 요금 합계에 들어가지 않았어요</span>}
    </p>
  );
}

const SOURCE_HINT: Record<string, string> = {
  reported: "제공사가 응답에 적어 준 요금 그대로 (OpenRouter)",
  priced: "응답의 토큰(캐시 읽기·쓰기·추론 포함)을 공식 단가로 계산 (OpenAI·Google)",
  free: "자체 서버 — API 요금 없음",
  unpriced: "단가표에 없는 모델 — 요금에 넣지 않음",
  estimated: "요금 장부 이전 기록 — 캐시 적중을 몰라 예전 추정으로 계산",
};

/** 두세 개 중 하나를 고르는 토글 (버튼 묶음) */
function Seg<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={value === o.value ? "active" : ""}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---------- 막대 (차트 라이브러리 없이 SVG) ----------
interface BarCol {
  key: string;
  label: string;
  /** 아래에서 위로 쌓는 조각 */
  segs: { value: number; cls: string }[];
  title: string;
}

const COL_W = 10;
const PLOT_H = 100;

/**
 * 칸마다 막대 하나. 막대는 SVG(가로·세로 늘어남), 글자는 HTML 이라 휴대폰 폭에서도 글자가 찌그러지지 않는다.
 * 막대·이름을 누르면 그 칸이 선택돼 아래 한 줄에 자세한 숫자가 나온다 (손가락으로는 툴팁을 볼 수 없으니)
 */
function MonthBars({
  cols,
  sel,
  onSelect,
  label,
  maxText,
  testId,
  dense = false,
}: {
  cols: BarCol[];
  sel: number | null;
  onSelect: (i: number) => void;
  label: string;
  maxText: (max: number) => string;
  testId: string;
  dense?: boolean;
}) {
  const max = Math.max(0, ...cols.map((c) => c.segs.reduce((s, x) => s + x.value, 0)));
  const scale = max > 0 ? (PLOT_H - 4) / max : 0;
  const n = Math.max(1, cols.length);
  return (
    <div className={`mbars${dense ? " dense" : ""}`} data-testid={testId}>
      <div className="mbars-plot">
        {max > 0 && <span className="mbars-max">{maxText(max)}</span>}
        <svg viewBox={`0 0 ${n * COL_W} ${PLOT_H}`} preserveAspectRatio="none" role="img" aria-label={label}>
          {[0.25, 0.5, 0.75].map((f) => (
            <line
              key={f}
              className="mbars-grid"
              x1={0}
              x2={n * COL_W}
              y1={PLOT_H - (PLOT_H - 4) * f}
              y2={PLOT_H - (PLOT_H - 4) * f}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {cols.map((c, i) => {
            let y = PLOT_H;
            return (
              <g key={c.key} data-col={c.key} onClick={() => onSelect(i)} className="mbars-col">
                <title>{c.title}</title>
                <rect
                  className={i === sel ? "mbars-hit on" : "mbars-hit"}
                  x={i * COL_W}
                  y={0}
                  width={COL_W}
                  height={PLOT_H}
                />
                {c.segs.map((s) => {
                  const h = s.value * scale;
                  y -= h;
                  return h > 0 ? (
                    <rect
                      key={s.cls}
                      className={s.cls}
                      data-seg={s.cls}
                      data-value={s.value}
                      x={i * COL_W + 1.6}
                      y={y}
                      width={COL_W - 3.2}
                      height={h}
                    />
                  ) : null;
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="mbars-labels" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
        {cols.map((c, i) => (
          <button
            key={c.key}
            type="button"
            className={i === sel ? "active" : ""}
            aria-pressed={i === sel}
            aria-label={c.title.split("\n")[0]}
            title={c.title}
            onClick={() => onSelect(i)}
          >
            {c.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function segsOf(t: Totals, metric: Metric) {
  if (metric === "cost") return [{ value: t.cost, cls: "cost-spent" }];
  const s = tokenStack(t);
  return [
    { value: s.cached, cls: "llm-cached" },
    { value: s.rest, cls: "llm-input" },
    { value: s.output, cls: "llm-output" },
  ];
}

function titleOf(head: string, t: Totals, money: (v: number) => string) {
  return [
    head,
    `요금 ${money(t.cost)}${t.unpriced ? ` (+단가 모름 ${fmtNum(t.unpriced)}회)` : ""}`,
    `호출 ${fmtNum(t.calls)}회`,
    `입력 ${fmtNum(t.input)} (캐시 읽기 ${fmtNum(t.cache_read)}) · 출력 ${fmtNum(t.output)} 토큰`,
  ].join("\n");
}

function Bars({
  keys,
  totals,
  sel,
  onSelect,
  colLabel,
  title,
  metric,
  money,
  testId,
  dense,
}: {
  keys: string[];
  totals: Map<string, Totals>;
  sel: number | null;
  onSelect: (i: number) => void;
  colLabel: (k: string, i: number) => string;
  title: (k: string) => string;
  metric: Metric;
  money: (v: number) => string;
  testId: string;
  dense?: boolean;
}) {
  const zero = sum([]);
  const cols: BarCol[] = keys.map((k, i) => {
    const t = totals.get(k) ?? zero;
    return { key: k, label: colLabel(k, i), segs: segsOf(t, metric), title: titleOf(title(k), t, money) };
  });
  const k = sel != null ? keys[sel] : null;
  const t = (k && totals.get(k)) || zero;
  return (
    <>
      <MonthBars
        cols={cols}
        sel={sel}
        onSelect={onSelect}
        label={metric === "cost" ? "요금" : "토큰 (캐시 읽기 · 입력 나머지 · 출력)"}
        maxText={(v) => (metric === "cost" ? money(v) : `${fmtTokens(v)} 토큰`)}
        testId={testId}
        dense={dense}
      />
      {k && (
        <p className="mbars-detail" data-testid={`${testId}-detail`}>
          <b>{title(k)}</b>
          <span>
            <i className="sw cost-spent" /> {money(t.cost)}
            {t.unpriced > 0 && <small className="muted"> +단가 모름 {fmtNum(t.unpriced)}회</small>}
          </span>
          <span>
            <i className="sw llm-cached" /> 캐시 읽기 {fmtTokens(t.cache_read)}
          </span>
          <span>
            <i className="sw llm-input" /> 입력 나머지 {fmtTokens(Math.max(0, t.input - t.cache_read))}
          </span>
          <span>
            <i className="sw llm-output" /> 출력 {fmtTokens(t.output)}
          </span>
          <span className="muted">호출 {fmtNum(t.calls)}회</span>
        </p>
      )}
    </>
  );
}

function DayBars({
  data,
  filters,
  metric,
  money,
  sel,
  onSelect,
}: {
  data: LlmCosts;
  filters: Filters;
  metric: Metric;
  money: (v: number) => string;
  sel: number | null;
  onSelect: (i: number | null) => void;
}) {
  const days = daysOf(data.month, data.today);
  if (days.length === 0) return <p className="muted">이 달은 아직 날짜가 없어요.</p>;
  const totals = groupBy(data.daily, (g) => g.day, filters);
  return (
    <Bars
      keys={days}
      totals={totals}
      sel={sel}
      onSelect={(i) => onSelect(i === sel ? null : i)}
      colLabel={(d) => {
        const n = Number(d.slice(8));
        return n === 1 || n % 5 === 0 ? String(n) : "";
      }}
      title={dayLong}
      metric={metric}
      money={money}
      testId="day-chart"
      dense
    />
  );
}

// ---------- 나눠 보기 ----------
function rowKey(g: LlmLedgerGroup & { day?: string }, by: By): string {
  if (by === "model") return `${g.provider}|${g.model}|${g.profile}`;
  if (by === "account") return `${g.market}|${g.strategy}`;
  if (by === "kind") return g.kind;
  return g.day ?? "";
}

function rowLabel(key: string, by: By, data: LlmCosts): ReactNode {
  if (by === "model") {
    const [provider, model, profile] = key.split("|");
    return (
      <span className="costs-row-name">
        <b>{model || "모름"}</b>
        <small className="muted">
          {providerLabel(provider)} · {profileLabel(profile)}
        </small>
      </span>
    );
  }
  if (by === "account") {
    const [market, strategy] = key.split("|");
    return <b>{accountLabel(market, strategy, data.strategies)}</b>;
  }
  if (by === "kind") return <b>{kindLabel(key)}</b>;
  return <b>{dayLong(key)}</b>;
}

function Breakdown({
  data,
  groups,
  filters,
  by,
  money,
}: {
  data: LlmCosts;
  groups: LlmLedgerGroup[];
  filters: Filters;
  by: By;
  money: (v: number) => string;
}) {
  const src: (LlmLedgerGroup & { day?: string })[] = by === "day" ? data.daily : groups;
  const rows = [...groupBy(src, (g) => rowKey(g, by), filters).entries()].sort((a, b) =>
    by === "day" ? b[0].localeCompare(a[0]) : b[1].cost - a[1].cost || b[1].input - a[1].input,
  );
  const total = sum(src, filters);
  if (rows.length === 0) return <p className="muted">고른 조건에 맞는 기록이 없어요.</p>;
  return (
    <div className="table-wrap">
      <table className="table costs-breakdown" data-testid="costs-breakdown">
        <thead>
          <tr>
            <th>{by === "model" ? "모델" : by === "account" ? "계좌" : by === "kind" ? "종류" : "날짜"}</th>
            <th className="num">호출</th>
            <th className="num">입력</th>
            <th className="num">캐시 읽기</th>
            <th className="num">출력 (추론)</th>
            <th className="num">요금</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, t]) => (
            <tr key={k}>
              <td>{rowLabel(k, by, data)}</td>
              <td className="num">{fmtNum(t.calls)}</td>
              <td className="num" title={fmtNum(t.input)}>
                {fmtTokens(t.input)}
              </td>
              <td
                className="num"
                title={t.cache_known < t.calls ? "캐시 읽기를 알려 주지 않은 호출이 있어요" : undefined}
              >
                {fmtTokens(t.cache_read)}
                {t.cache_known < t.calls && t.calls > 0 ? <small className="muted">＊</small> : null}
              </td>
              <td className="num" title={fmtNum(t.output)}>
                {fmtTokens(t.output)}
                {t.reasoning > 0 && <small className="muted"> ({fmtTokens(t.reasoning)})</small>}
              </td>
              <td className="num">
                <b>{money(t.cost)}</b>
                {t.unpriced > 0 && (
                  <small className="muted costs-unpriced"> +단가 모름 {fmtNum(t.unpriced)}회</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th>합계</th>
            <td className="num">{fmtNum(total.calls)}</td>
            <td className="num">{fmtTokens(total.input)}</td>
            <td className="num">{fmtTokens(total.cache_read)}</td>
            <td className="num">{fmtTokens(total.output)}</td>
            <td className="num">
              <b>{money(total.cost)}</b>
            </td>
          </tr>
        </tfoot>
      </table>
      <p className="muted costs-small">
        입력에는 캐시 읽기가 들어 있어요. 출력 괄호는 그중 추론 토큰. ＊ 표시는 캐시 읽기를 알려 주지 않은
        호출(자체 서버 등)이 섞여 있다는 뜻이에요.
      </p>
    </div>
  );
}

// ---------- 공식 기록 대조 ----------
const STATUS_KO: Record<LlmOfficialProvider["status"], string> = {
  ok: "맞춰 봄",
  no_key: "키 없음",
  unsupported: "공식 조회 없음",
  error: "조회 실패",
};

function Official({ data, money }: { data: LlmCosts; money: (v: number) => string }) {
  const q = useLlmOfficial(data.month);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    setBusy(true);
    try {
      const fresh = await get<LlmOfficial>(
        `/api/llm/costs/official?month=${encodeURIComponent(data.month)}&refresh=true`,
      );
      qc.setQueryData(["llm-official", data.month], fresh);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card costs-official" aria-label="공식 기록과 맞춰 보기">
      <div className="card-head">
        <h2>공식 기록과 맞춰 보기 · {monthLong(data.month)}</h2>
        <button type="button" className="btn ghost" onClick={refresh} disabled={busy || q.isFetching}>
          {busy ? "불러오는 중…" : "다시 불러오기"}
        </button>
      </div>
      {q.isPending ? (
        <p className="muted">공식 기록 불러오는 중…</p>
      ) : q.isError || !q.data ? (
        <p className="error">불러오지 못했어요: {errorText(q.error)}</p>
      ) : (
        <ul className="costs-official-list">
          {q.data.providers.map((p) => (
            <li key={p.provider} data-testid={`official-${p.provider}`}>
              <div className="costs-official-head">
                <b>{p.label}</b>
                <span className={`pill st-${p.status}`}>{STATUS_KO[p.status]}</span>
              </div>
              <p className="muted costs-small">{p.note}</p>
              <OfficialBody p={p} money={money} />
            </li>
          ))}
        </ul>
      )}
      <p className="muted costs-small">
        공식 기록은 UTC 날짜 기준이라, 위 한국 날짜 합계와 하루 경계가 달라요.
      </p>
    </section>
  );
}

function Diff({ a, b, money }: { a: number; b: number; money: (v: number) => string }) {
  const d = b - a;
  if (Math.abs(d) < 0.00005) return <span className="muted">같음</span>;
  return <span className={d > 0 ? "up" : "down"}>{`${d > 0 ? "+" : "-"}${money(Math.abs(d))}`}</span>;
}

function OfficialBody({ p, money }: { p: LlmOfficialProvider; money: (v: number) => string }) {
  if (p.windows?.length) {
    return (
      <table className="table compact costs-official-table">
        <thead>
          <tr>
            <th>기간</th>
            <th className="num">공식</th>
            <th className="num">장부</th>
            <th className="num">공식 − 장부</th>
          </tr>
        </thead>
        <tbody>
          {p.windows.map((w) => (
            <tr key={w.key}>
              <td>{w.label}</td>
              <td className="num">{money(w.official.cost)}</td>
              <td className="num">{money(w.ledger.cost)}</td>
              <td className="num">
                <Diff a={w.ledger.cost} b={w.official.cost} money={money} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  if (p.rows?.length) {
    return (
      <table className="table compact costs-official-table">
        <thead>
          <tr>
            <th>모델</th>
            <th className="num">요청 (공식/장부)</th>
            <th className="num">입력 (공식/장부)</th>
            <th className="num">공식 요금</th>
            <th className="num">장부 요금</th>
          </tr>
        </thead>
        <tbody>
          {p.rows.map((r) => (
            <tr key={r.model}>
              <td>{r.model}</td>
              <td className="num">
                {r.official ? fmtNum(r.official.requests) : "—"} / {fmtNum(r.ledger.calls)}
              </td>
              <td className="num">
                {r.official ? fmtTokens(r.official.input) : "—"} / {fmtTokens(r.ledger.input)}
              </td>
              <td className="num">{r.official ? money(r.official.cost) : "—"}</td>
              <td className="num">{money(r.ledger.cost)}</td>
            </tr>
          ))}
          {(p.official_other ?? 0) > 0 && (
            <tr>
              <td className="muted">모델 밖 항목 (도구 등)</td>
              <td />
              <td />
              <td className="num">{money(p.official_other ?? 0)}</td>
              <td />
            </tr>
          )}
        </tbody>
      </table>
    );
  }
  if (p.ledger) {
    return (
      <p className="costs-small">
        장부 (UTC 달): 호출 {fmtNum(p.ledger.calls)}회 · {money(p.ledger.cost)}
      </p>
    );
  }
  return null;
}

function Footnotes({ data, rate }: { data: LlmCosts; rate: number | null }) {
  const links: ReactNode[] = PROVIDERS.flatMap((pv) => {
    const url = safeUrl(data.sources[pv.id]);
    return url
      ? [
          <a key={pv.id} href={url} target="_blank" rel="noopener noreferrer">
            {pv.label}
          </a>,
        ]
      : [];
  });
  return (
    <section className="card costs-notes">
      <h3>요금은 이렇게 정해요</h3>
      <ul>
        <li>
          <b>OpenRouter</b>: 응답마다 OpenRouter 가 적어 준 요금(usage.cost)을 그대로 남겨요. 캐시·추론이 이미
          반영돼 있어요.
        </li>
        <li>
          <b>OpenAI·Google</b>: 응답의 토큰으로 공식 단가를 곱해요. 캐시 읽기는 캐시 단가, OpenAI 캐시 쓰기는
          입력의 1.25배(30분)·2.5배(12시간), 추론 토큰은 출력 단가예요. Google 이 출력에 넣지 않은 생각 토큰은
          합계에서 찾아 출력 단가로 더해요.
        </li>
        <li>
          <b>자체 서버(무료)</b>는 0원이에요. 단가표에 없는 모델은 0원으로 치지 않고 ‘단가 모름’으로 따로
          세요.
        </li>
        <li>
          요금 장부 전에 쌓인 기록은 캐시 적중을 알 수 없어 예전 추정으로 계산했어요(‘예전 기록 추정’).
          국장·미장·전략 실험실·뉴스 룸 기록을 한 장부에 모았어요.
        </li>
        <li>
          단가는 각 회사 공식 가격표 기준이에요
          {links.length > 0 && (
            <>
              {" "}
              (
              {links.map((l, i) => (
                <span key={i}>
                  {i > 0 && " · "}
                  {l}
                </span>
              ))}
              )
            </>
          )}
          . 확인일 {data.checked}. 예정된 인상은 그날부터 반영해요.
        </li>
        <li>
          환율은 토스 원/달러 기준이에요
          {rate ? ` ($1 = ${fmtIndicator(rate, "원")})` : " (지금은 받지 못해 $ 로만 보여요)"}.
        </li>
      </ul>
    </section>
  );
}
