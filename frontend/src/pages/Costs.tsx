import { useState, type ReactNode } from "react";
import { errorText } from "../api/client";
import type { LlmCosts, LlmModelCost, LlmScenario, LlmUsageMonth } from "../api/types";
import { Loading } from "../components/Loading";
import { useLlmCosts } from "../hooks";
import {
  extremes,
  isEmptyUsage,
  monthLabel,
  monthLong,
  PROVIDERS,
  providerLabel,
  safeUrl,
  sortModels,
  spentThisMonth,
  tokenStack,
  type SortDir,
  type SortKey,
} from "../utils/costs";
import { fmtIndicator, fmtKrwFromUsd, fmtNum, fmtTokens, fmtUsd, fmtUsdPrice } from "../utils/format";

type Currency = "krw" | "usd";

const SCENARIO_KO: Record<LlmScenario, string> = { cache: "캐시 적용(추정)", nocache: "캐시 없음" };
const BACKFILL_NOTE = "예전 세션 합계 포함 — 호출별 캐시 추정 없음";

/** LLM 비용 예상: 지금 토큰 사용량을 상용 모델(Anthropic·OpenAI·Google) 요금으로 환산 */
export function Costs() {
  const { data, isPending, isError, error } = useLlmCosts();
  if (isPending) return <Loading cards={3} />;
  if (isError || !data) {
    return (
      <section className="card">
        <h2>💸 LLM 비용 예상</h2>
        <p className="error">불러오지 못했어요: {errorText(error)}</p>
      </section>
    );
  }
  return <CostsView data={data} />;
}

function CostsView({ data }: { data: LlmCosts }) {
  const [scenario, setScenario] = useState<LlmScenario>("cache");
  const [cur, setCur] = useState<Currency>("krw");
  const [modelId, setModelId] = useState(data.models[0]?.id ?? "");
  const [sel, setSel] = useState(data.months.length - 1);

  const rate = data.usd_krw != null && data.usd_krw > 0 ? data.usd_krw : null;
  const currency: Currency = rate ? cur : "usd";
  const money = (v: number) => (currency === "krw" && rate ? fmtKrwFromUsd(v, rate) : fmtUsd(v));
  const empty = isEmptyUsage(data.months);
  const model = data.models.find((m) => m.id === modelId) ?? data.models[0];

  return (
    <div className="stack costs-page">
      <section className="card costs-head">
        <div className="card-head">
          <h2>💸 LLM 비용 예상</h2>
          <span className="muted">가격 확인일 {data.checked}</span>
        </div>
        <p className="costs-intro">
          {data.current_model && data.profile === "paid" ? (
            <>
              지금 쓰는 모델 <span className="pill paid">유료</span>{" "}
              <b className="costs-model">{data.current_model}</b> — <b>호출마다 실제 API 요금이 나가요</b>.
            </>
          ) : data.current_model ? (
            <>
              지금 쓰는 모델 {data.profile === "free" && <span className="pill">무료</span>}{" "}
              <b className="costs-model">{data.current_model}</b> — 자체 서버라 <b>지금은 API 요금 없음</b>.
            </>
          ) : (
            <>
              LLM 이 설정돼 있지 않아요(규칙 기반) — <b>지금은 API 요금 없음</b>.
            </>
          )}{" "}
          <span className="muted">
            같은 사용량을 상용 모델로 바꾸면 한 달에 얼마가 드는지 입력 처리·출력 생성으로 나눠 미리 봐요.
          </span>
        </p>
        <div className="costs-toggles">
          <Seg
            label="캐시 시나리오"
            value={scenario}
            onChange={setScenario}
            options={[
              { value: "cache", label: SCENARIO_KO.cache },
              { value: "nocache", label: SCENARIO_KO.nocache },
            ]}
          />
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

      <section className="card">
        <div className="card-head">
          <h2>월별 토큰 사용량</h2>
          {!empty && (
            <div className="costs-legend" aria-label="범례">
              <span>
                <i className="sw llm-cached" /> 입력(캐시 적중 추정)
              </span>
              <span>
                <i className="sw llm-input" /> 입력(나머지)
              </span>
              <span>
                <i className="sw llm-output" /> 출력
              </span>
            </div>
          )}
        </div>
        {empty ? (
          <p className="muted empty" data-testid="costs-empty">
            아직 기록된 LLM 사용량이 없어요. 기록은 이번 업데이트부터 쌓이고, 예전 판단 세션은 세션 합계로
            가져왔어요.
          </p>
        ) : (
          <TokenChart months={data.months} sel={sel} onSelect={setSel} />
        )}
      </section>

      {!empty && model && (
        <ModelPanel
          data={data}
          model={model}
          onModel={setModelId}
          scenario={scenario}
          money={money}
          sel={sel}
          onSelect={setSel}
        />
      )}

      <CompareTable
        data={data}
        scenario={scenario}
        money={money}
        selectedId={model?.id}
        onModel={setModelId}
      />

      <Footnotes data={data} rate={rate} />
    </div>
  );
}

/** 두 개 중 하나를 고르는 토글 (버튼 묶음) */
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

// ---------- 월별 막대 (차트 라이브러리 없이 SVG) ----------
interface BarCol {
  key: string;
  label: string;
  /** 아래에서 위로 쌓는 조각 */
  segs: { value: number; cls: string }[];
  title: string;
  mark?: boolean;
}

const COL_W = 10;
const PLOT_H = 100;

/**
 * 달마다 막대 하나. 막대는 SVG(가로·세로 늘어남), 글자는 HTML 이라 휴대폰 폭에서도 글자가 찌그러지지 않는다.
 * 막대·달 이름을 누르면 그 달이 선택돼 아래 한 줄에 자세한 숫자가 나온다 (손가락으로는 툴팁을 볼 수 없으니)
 */
function MonthBars({
  cols,
  sel,
  onSelect,
  label,
  maxText,
  testId,
}: {
  cols: BarCol[];
  sel: number;
  onSelect: (i: number) => void;
  label: string;
  maxText: (max: number) => string;
  testId: string;
}) {
  const max = Math.max(0, ...cols.map((c) => c.segs.reduce((s, x) => s + x.value, 0)));
  const scale = max > 0 ? (PLOT_H - 4) / max : 0;
  const n = cols.length;
  return (
    <div className="mbars" data-testid={testId}>
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
              <g key={c.key} data-month={c.key} onClick={() => onSelect(i)} className="mbars-col">
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
            className={`${i === sel ? "active" : ""}${c.mark ? " mark" : ""}`}
            aria-pressed={i === sel}
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

function tokenTitle(m: LlmUsageMonth): string {
  const s = tokenStack(m);
  return [
    monthLong(m.month),
    `입력(캐시 적중 추정) ${fmtNum(s.cached)}`,
    `입력(나머지) ${fmtNum(s.rest)}`,
    `출력 ${fmtNum(s.output)}`,
    `합계 ${fmtNum(s.total)} 토큰 · 호출 ${fmtNum(m.calls)}회`,
    `세션 ${fmtNum(m.sessions)} · 답장 ${fmtNum(m.chats)}`,
    ...(m.backfill > 0 ? [`＊ ${BACKFILL_NOTE}`] : []),
  ].join("\n");
}

function TokenChart({
  months,
  sel,
  onSelect,
}: {
  months: LlmUsageMonth[];
  sel: number;
  onSelect: (i: number) => void;
}) {
  const cols: BarCol[] = months.map((m, i) => {
    const s = tokenStack(m);
    return {
      key: m.month,
      label: monthLabel(m.month, i),
      segs: [
        { value: s.cached, cls: "llm-cached" },
        { value: s.rest, cls: "llm-input" },
        { value: s.output, cls: "llm-output" },
      ],
      title: tokenTitle(m),
      mark: m.backfill > 0,
    };
  });
  const m = months[sel] ?? months[months.length - 1];
  const s = tokenStack(m);
  return (
    <>
      <MonthBars
        cols={cols}
        sel={sel}
        onSelect={onSelect}
        label="월별 토큰 사용량 (입력 캐시 적중 추정 · 입력 나머지 · 출력)"
        maxText={(v) => `${fmtTokens(v)} 토큰`}
        testId="token-chart"
      />
      <p className="mbars-detail" data-testid="token-detail">
        <b>{monthLong(m.month)}</b>
        <span>
          <i className="sw llm-cached" /> 캐시 적중 추정 {fmtNum(s.cached)}
        </span>
        <span>
          <i className="sw llm-input" /> 입력 나머지 {fmtNum(s.rest)}
        </span>
        <span>
          <i className="sw llm-output" /> 출력 {fmtNum(s.output)}
        </span>
        <span className="muted">
          호출 {fmtNum(m.calls)}회 · 세션 {fmtNum(m.sessions)} · 답장 {fmtNum(m.chats)}
        </span>
      </p>
      {months.some((x) => x.backfill > 0) && (
        <p className="muted costs-small">
          ＊ 표시한 달은 {BACKFILL_NOTE}
          {m.backfill > 0 ? ` (이 달 ${fmtNum(m.backfill)}건)` : ""}
        </p>
      )}
    </>
  );
}

// ---------- 모델 고르기 + 이번 달 예상 ----------
function ModelPanel({
  data,
  model,
  onModel,
  scenario,
  money,
  sel,
  onSelect,
}: {
  data: LlmCosts;
  model: LlmModelCost;
  onModel: (id: string) => void;
  scenario: LlmScenario;
  money: (v: number) => string;
  sel: number;
  onSelect: (i: number) => void;
}) {
  const p = data.projection;
  const spent = spentThisMonth(model, p.month, scenario);
  const projected = model.projection[scenario];
  const daily = model.daily_avg[scenario];
  const providerModels = data.models.filter((m) => m.provider === model.provider);
  const cols: BarCol[] = data.months.map((mo, i) => {
    const v = model.months[mo.month]?.[scenario] ?? 0;
    // 이번 달은 '남은 기간 예상'을 옅게 이어 쌓는다
    const rest = mo.month === p.month ? Math.max(0, projected - v) : 0;
    return {
      key: mo.month,
      label: monthLabel(mo.month, i),
      segs: [
        { value: v, cls: "cost-spent" },
        { value: rest, cls: "cost-proj" },
      ],
      title: [
        `${monthLong(mo.month)} · ${model.name}`,
        `${SCENARIO_KO[scenario]} ${money(v)}`,
        ...(rest > 0 ? [`이번 달 예상 ${money(projected)}`] : []),
        ...(mo.backfill > 0 ? [`＊ ${BACKFILL_NOTE}`] : []),
      ].join("\n"),
      mark: mo.backfill > 0,
    };
  });
  const selMonth = data.months[sel] ?? data.months[data.months.length - 1];

  return (
    <section className="card">
      <div className="card-head">
        <h2>모델별 예상 요금</h2>
        <span className="muted">{SCENARIO_KO[scenario]}</span>
      </div>
      <div className="costs-picker">
        <div className="seg" role="group" aria-label="회사">
          {PROVIDERS.map((pv) => {
            const first = data.models.find((m) => m.provider === pv.id);
            const on = model.provider === pv.id;
            return (
              <button
                key={pv.id}
                type="button"
                className={on ? "active" : ""}
                aria-pressed={on}
                disabled={!first}
                onClick={() => first && !on && onModel(first.id)}
              >
                {pv.label}
              </button>
            );
          })}
        </div>
        <div className="model-chips" role="group" aria-label="모델">
          {providerModels.map((m) => (
            <button
              key={m.id}
              type="button"
              className={m.id === model.id ? "active" : ""}
              aria-pressed={m.id === model.id}
              onClick={() => onModel(m.id)}
            >
              {m.name}
              <small>{m.tier}</small>
            </button>
          ))}
        </div>
      </div>

      <div className="costs-proj">
        <span className="kpi-label">
          {monthLong(p.month)} 예상 · {model.name}
        </span>
        <b className="costs-big" data-testid="projection">
          {money(projected)}
        </b>
        <span className="kpi-sub">
          {p.basis_days > 0 ? (
            <>
              오늘까지 {money(spent)} + 하루 {money(daily)} × 남은 거래일 {p.remaining_trading_days}일
              {p.method === "schedule" && model.per_session ? (
                <>
                  {" "}
                  (세션 1회 평균 {money(model.per_session[scenario])} × 지금 일정 하루 {p.sessions_per_day}회
                  + 답장 — 최근 {p.basis_window_days}일 세션 {p.basis_sessions}회 기준)
                </>
              ) : (
                <>
                  {" "}
                  (최근 {p.basis_window_days}일 중 사용한 {p.basis_days}일 기준)
                </>
              )}
            </>
          ) : (
            <>
              오늘까지 {money(spent)} (최근 {p.basis_window_days}일 동안 사용한 날이 없어 남은 거래일{" "}
              {p.remaining_trading_days}일은 더하지 않았어요)
            </>
          )}
        </span>
        <span className="costs-small muted">
          100만 토큰당 입력 {fmtUsdPrice(model.price.input)} · 캐시 입력 {fmtUsdPrice(model.price.cached)}
          {model.price.cache_write != null && <> · 캐시 쓰기 {fmtUsdPrice(model.price.cache_write)}</>} · 출력{" "}
          {fmtUsdPrice(model.price.output)}
          {model.price.long_from != null && (
            <> · 호출당 입력 {fmtTokens(model.price.long_from)} 토큰 초과 시 장문 요금</>
          )}
        </span>
        {model.note && <small className="muted">{model.note}</small>}
      </div>

      <MonthBars
        cols={cols}
        sel={sel}
        onSelect={onSelect}
        label={`${model.name} 월별 예상 요금`}
        maxText={money}
        testId="cost-chart"
      />
      <p className="mbars-detail">
        <b>{monthLong(selMonth.month)}</b>
        <span>
          <i className="sw cost-spent" /> {money(model.months[selMonth.month]?.[scenario] ?? 0)}
        </span>
        {selMonth.month === p.month && (
          <span>
            <i className="sw cost-proj" /> 이번 달 예상 {money(projected)}
          </span>
        )}
      </p>
    </section>
  );
}

// ---------- 12개 모델 비교표 ----------
const SORT_LABEL: Record<SortKey, string> = {
  name: "모델",
  input: "입력",
  output: "출력",
  spent: "지금까지",
  projection: "이번 달 예상",
};

function CompareTable({
  data,
  scenario,
  money,
  selectedId,
  onModel,
}: {
  data: LlmCosts;
  scenario: LlmScenario;
  money: (v: number) => string;
  selectedId: string | undefined;
  onModel: (id: string) => void;
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "projection", dir: "asc" });
  const month = data.projection.month;
  const rows = sortModels(data.models, sort.key, sort.dir, month, scenario);
  const { cheapest, priciest } = extremes(data.models, scenario);
  const maxProj = Math.max(0, ...data.models.map((m) => m.projection[scenario]));

  const head = (key: SortKey, className = "") => {
    const on = sort.key === key;
    return (
      <th
        className={className}
        aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
      >
        <button
          type="button"
          className="sort"
          onClick={() =>
            setSort(on ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" })
          }
        >
          {SORT_LABEL[key]}
          <span aria-hidden>{on ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</span>
        </button>
      </th>
    );
  };

  return (
    <section className="card">
      <div className="card-head">
        <h2>12개 모델 비교</h2>
        <span className="muted">
          {monthLong(month)} · {SCENARIO_KO[scenario]} · 가격은 100만 토큰당 $
        </span>
      </div>
      <div className="table-scroll">
        <table className="table costs-table" data-testid="costs-table">
          <thead>
            <tr>
              <th>회사</th>
              {head("name")}
              <th>등급</th>
              {head("input", "r")}
              <th className="r">캐시 입력</th>
              {head("output", "r")}
              {head("spent", "r")}
              {head("projection", "r")}
              <th aria-label="예상 요금 막대" />
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => {
              const proj = m.projection[scenario];
              const tag = m.id === cheapest ? "cheapest" : m.id === priciest ? "priciest" : "";
              return (
                <tr
                  key={m.id}
                  data-model={m.id}
                  className={[tag, m.id === selectedId ? "selected" : ""].filter(Boolean).join(" ")}
                >
                  <td>{providerLabel(m.provider)}</td>
                  <td className="wrap costs-name">
                    <button type="button" className="link" onClick={() => onModel(m.id)}>
                      {m.name}
                    </button>
                    {tag === "cheapest" && <span className="pill cheap">가장 저렴</span>}
                    {tag === "priciest" && <span className="pill pricey">가장 비쌈</span>}
                    {m.note && <small className="muted costs-note">{m.note}</small>}
                  </td>
                  <td>{m.tier}</td>
                  <td className="r">{fmtUsdPrice(m.price.input)}</td>
                  <td className="r">
                    {fmtUsdPrice(m.price.cached)}
                    {m.price.cache_write != null && (
                      <small className="muted costs-note">쓰기 {fmtUsdPrice(m.price.cache_write)}</small>
                    )}
                  </td>
                  <td className="r">{fmtUsdPrice(m.price.output)}</td>
                  <td className="r">{money(spentThisMonth(m, month, scenario))}</td>
                  <td className="r costs-proj-cell">
                    <b>{money(proj)}</b>
                  </td>
                  <td className="costs-bar-cell">
                    <span className="cost-bar" aria-hidden>
                      <i style={{ width: `${maxProj > 0 ? (proj / maxProj) * 100 : 0}%` }} />
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
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
      <h3>읽기 전에</h3>
      <ul>
        <li>
          토큰 수는 지금 모델 토크나이저 기준이라, 다른 모델은 같은 글도 토큰 수가 달라 실제 청구액과 차이가
          날 수 있어요.
        </li>
        <li>
          ‘캐시 적용’은 세션 안에서 바로 전 호출의 입력이 다음 호출에 그대로 들어가는 만큼을 캐시로 본
          추정이에요. OpenAI·Google 은 자동으로 캐시되고, Anthropic 은 캐시 설정이 필요하며 캐시 쓰기
          요금(입력의 1.25배)이 붙어요.
        </li>
        <li>
          가격은 각 회사 공식 가격표 기준이에요
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
          . 확인일 {data.checked}.
        </li>
        <li>예정된 가격 인상은 해당 월부터 반영해요 (표의 가격은 오늘 기준).</li>
        <li>
          환율은 토스 원/달러 기준이에요
          {rate ? ` ($1 = ${fmtIndicator(rate, "원")})` : " (지금은 받지 못해 $ 로만 보여요)"}.
        </li>
      </ul>
    </section>
  );
}
