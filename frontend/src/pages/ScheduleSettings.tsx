import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, del, errorText, put } from "../api/client";
import type { MarketCode, ScheduleInfo, ScheduleUpdate } from "../api/types";
import { invalidateNames, MARKET_META, useMarket } from "../market";
import { toast } from "../ui/toast";
import { scheduleKey, scheduleQuery } from "./settingsQueries";
import { fmtDateTime, JOB_KO } from "../utils/format";
import {
  EXT_CUTOFF_MINUTES,
  extSessionTimes,
  inWindow,
  intervalLabel,
  kstShift,
  kstText,
  MARKET_TABS,
  outsideMarketTimes,
  SCHEDULE_API,
  scheduleTimes,
  TIME_OPTIONS,
  validateSchedule,
} from "../utils/schedule";

const is404 = (e: unknown) => e instanceof ApiError && e.status === 404;
/** 서버가 intervals 를 비워 보낼 때 쓰는 기본 목록 */
const FALLBACK_INTERVALS = [5, 10, 15, 20, 30, 60, 120, 180, 240];
/** 미리 보기 칩은 이만큼만 그린다 (나머지는 '외 N개') */
const MAX_CHIPS = 120;
const OUTSIDE_TEXT = "장 시간 밖 — 분석만 하고 주문은 다음 장에";

/** ⏰ 판단 주기 — 시장별로 판단 세션을 도는 시간대와 주기를 정한다 */
export function ScheduleSettings() {
  const kr = useQuery(scheduleQuery("KR"));
  // 미장이 꺼져 있으면 404 — 그때는 '미장' 탭을 숨긴다
  const us = useQuery(scheduleQuery("US"));
  // 처음에는 위 전환에서 보고 있는 시장 탭으로 연다
  const { market: viewing } = useMarket();
  const [market, setMarket] = useState<MarketCode>(viewing);

  // 예전 서버(이 기능이 없음)면 카드 자체를 그리지 않는다
  if (is404(kr.error)) return null;

  const usVisible = !!us.data || (us.isError && !is404(us.error));
  const current: MarketCode = market === "US" && !usVisible ? "KR" : market;
  const q = current === "KR" ? kr : us;

  return (
    <section className="card schedule-card" aria-labelledby="schedule-title">
      <div className="card-head">
        <h2 id="schedule-title">판단 주기</h2>
        <div className="seg sched-tabs" role="tablist" aria-label="시장">
          {MARKET_TABS.filter((t) => t.key === "KR" || usVisible).map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={current === t.key}
              className={current === t.key ? "active" : ""}
              onClick={() => setMarket(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <p className="muted sched-lead">
        판단은 <b>자정부터 주기 단위로 센 딱 떨어지는 시각</b>에만 돌아요. 예) 08:00~17:00 · 1시간 → 08:00,
        09:00, …, 17:00 / 15분 → 08:00, 08:15, 08:30, … 15:33에 설정을 바꿔도 15:33에 바로 돌지 않고, 다음
        슬롯부터 적용돼요.
      </p>
      {q.isLoading && <p className="muted">불러오는 중…</p>}
      {q.isError && <p className="error">판단 주기를 불러오지 못했어요: {errorText(q.error)}</p>}
      {q.data && <ScheduleEditor key={current} market={current} data={q.data} />}
    </section>
  );
}

function ScheduleEditor({ market, data }: { market: MarketCode; data: ScheduleInfo }) {
  const qc = useQueryClient();
  const ids = useId();
  // 사용자가 바꾼 칸만 담는다 — 저장·되돌리기 뒤에는 비워서 서버 값을 그대로 보여 준다
  const [draft, setDraft] = useState<Partial<ScheduleUpdate>>({});

  const applied = (d: ScheduleInfo) => {
    qc.setQueryData(scheduleKey(market), d);
    setDraft({});
    // 판단 시각을 보여 주는 화면(상단·에이전트 구조)과 하루 세션 수로 계산하는 비용 예상도 다시 받는다
    invalidateNames(qc, ["system", "agent-structure", "llm-costs", "markets", "overview"], market);
  };
  const save = useMutation({
    mutationFn: (body: ScheduleUpdate) => put<ScheduleInfo>(SCHEDULE_API[market], body),
    onSuccess: (d) => {
      reset.reset();
      applied(d);
      const m = MARKET_META[market];
      toast(`${m.flag} ${m.name} 판단 일정을 저장했어요 다음 슬롯부터 적용돼요.`);
    },
  });
  const reset = useMutation({
    mutationFn: () => del<ScheduleInfo>(SCHEDULE_API[market]),
    onSuccess: (d) => {
      save.reset();
      applied(d);
      toast("기본값으로 되돌렸어요");
    },
  });

  const v: ScheduleUpdate = {
    start: data.start,
    end: data.end,
    interval_minutes: data.interval_minutes,
    ...draft,
  };
  const dirty = v.start !== data.start || v.end !== data.end || v.interval_minutes !== data.interval_minutes;
  const intervals = data.intervals.length > 0 ? data.intervals : FALLBACK_INTERVALS;
  const problem = validateSchedule(v.start, v.end, v.interval_minutes, intervals, data.max_sessions);
  // '기본값으로'가 되돌릴 값 (서버가 알려 줄 때만)
  const def = data.default;
  const busy = save.isPending || reset.isPending;
  const kst = data.timezone === "Asia/Seoul";
  const shift = kstShift(data);

  // 저장된 값 그대로면 서버가 계산한 시각을, 바꾸는 중이면 같은 규칙으로 화면에서 계산한다
  const times = dirty ? scheduleTimes(v.start, v.end, v.interval_minutes) : data.times;
  // 켠 장외 세션(D-61): 그 안에서도 같은 주기로 판단한다 — 일정 범위와 겹치는 시각은 한 번만
  const extSessions = data.extended ?? [];
  const inBase = new Set(times);
  const cutoff = data.ext_cutoff_minutes ?? EXT_CUTOFF_MINUTES;
  const extRows = extSessions.map((x) => ({
    ...x,
    times: dirty
      ? extSessionTimes(x.start, x.end, v.interval_minutes, cutoff).filter((t) => !inBase.has(t))
      : x.times,
  }));
  const extCount = extRows.reduce((n, x) => n + x.times.length, 0);
  const outside = new Set(
    dirty
      ? outsideMarketTimes(times, data.market_hours).filter(
          (t) => !extSessions.some((x) => inWindow(t, x.start, x.end)),
        )
      : data.outside_market,
  );
  const shown = times.slice(0, MAX_CHIPS);

  const edit = (patch: Partial<ScheduleUpdate>) => {
    save.reset();
    reset.reset();
    setDraft((d) => ({ ...d, ...patch }));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!dirty || busy || problem) return;
    save.mutate(v);
  };
  const toDefault = () => {
    if (data.custom) reset.mutate();
    else {
      setDraft({});
      save.reset();
    }
  };

  const timeOptions = (cur: string) => (TIME_OPTIONS.includes(cur) ? TIME_OPTIONS : [cur, ...TIME_OPTIONS]);
  const optionText = (t: string) => (kst ? t : `${t} (한국 ${kstText(t, shift)})`);
  const intervalOptions = intervals.includes(v.interval_minutes)
    ? intervals
    : [...intervals, v.interval_minutes].sort((a, b) => a - b);
  const hours = data.market_hours;

  return (
    <form
      className="form sched-form"
      onSubmit={submit}
      aria-label={`${market === "KR" ? "국장" : "미장"} 판단 주기`}
    >
      <div className="sched-status">
        <span className={`pill ${data.custom ? "llm-src screen" : ""}`}>
          {data.custom ? "화면에서 정한 값" : "기본값 (.env)"}
        </span>
        <small className="muted">
          장 시간 {hours.open}~{hours.close} · 주문 마감 {hours.order_cutoff} ({data.tz_label}
          {kst ? "" : ` · 한국 ${kstText(hours.open, shift)}~${kstText(hours.close, shift)}`}) · 마감 리포트{" "}
          {data.report_at}
        </small>
      </div>

      <div className="sched-fields">
        <div className="sched-field">
          <label htmlFor={`${ids}-start`}>
            시작 <small>({data.tz_label})</small>
          </label>
          <select
            id={`${ids}-start`}
            value={v.start}
            disabled={busy}
            onChange={(e) => edit({ start: e.target.value })}
          >
            {timeOptions(v.start).map((t) => (
              <option key={t} value={t}>
                {optionText(t)}
              </option>
            ))}
          </select>
        </div>
        <div className="sched-field">
          <label htmlFor={`${ids}-end`}>
            끝 <small>({data.tz_label})</small>
          </label>
          <select
            id={`${ids}-end`}
            value={v.end}
            disabled={busy}
            onChange={(e) => edit({ end: e.target.value })}
          >
            {timeOptions(v.end).map((t) => (
              <option key={t} value={t}>
                {optionText(t)}
              </option>
            ))}
          </select>
        </div>
        <div className="sched-field">
          <label htmlFor={`${ids}-interval`}>주기</label>
          <select
            id={`${ids}-interval`}
            value={v.interval_minutes}
            disabled={busy}
            onChange={(e) => edit({ interval_minutes: Number(e.target.value) })}
          >
            {intervalOptions.map((n) => (
              <option key={n} value={n}>
                {intervalLabel(n)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="sched-preview" role="group" aria-label="판단 시각 미리 보기">
        <div className="sched-preview-head">
          <b data-testid="sched-count">하루 {times.length + extCount}회</b>
          {extCount > 0 && (
            <small className="muted">
              (일정 {times.length} + 장외 {extCount})
            </small>
          )}
          <span className="muted">{dirty ? "저장하면 이렇게 바뀌어요" : "지금 일정"}</span>
        </div>
        {times.length > 0 ? (
          <ul className="time-chips sched-chips" aria-label="판단 시각">
            {shown.map((t) => {
              const out = outside.has(t);
              return (
                <li
                  key={t}
                  className={out ? "chip warn sched-out" : "chip"}
                  title={out ? OUTSIDE_TEXT : undefined}
                >
                  {t}
                  {!kst && <small className="sched-kst">한국 {kstText(t, shift)}</small>}
                </li>
              );
            })}
            {times.length > shown.length && (
              <li className="chip muted">외 {times.length - shown.length}개</li>
            )}
          </ul>
        ) : (
          <p className="muted sched-none">이 설정에는 판단 시각이 없어요.</p>
        )}
        {extRows.length > 0 && (
          <div className="sched-ext" role="group" aria-label="장외 세션 판단">
            {extRows.map((x) => (
              <div key={x.key} className="sched-ext-row">
                <div className="sched-ext-head">
                  <b>{x.label}</b>
                  <small className="muted num">
                    {x.start}~{x.end}
                    {kst ? "" : ` · 한국 ${x.start_kst}~${x.end_kst}`}
                  </small>
                  <small className="muted">{x.times.length}회</small>
                </div>
                {x.times.length > 0 ? (
                  <ul className="time-chips sched-chips" aria-label={`${x.label} 판단 시각`}>
                    {x.times.slice(0, MAX_CHIPS).map((t) => (
                      <li key={t} className="chip sched-ext-chip">
                        {t}
                        {!kst && <small className="sched-kst">한국 {kstText(t, shift)}</small>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted sched-none">이 주기로는 세션 안에 따로 도는 시각이 없어요.</p>
                )}
              </div>
            ))}
          </div>
        )}
        {extRows.length === 0 && (
          <p className="muted sched-cost">
            아래 장외 거래를 켜면 그 세션에서도 같은 주기로 판단하고 주문해요.
          </p>
        )}
        {outside.size > 0 && (
          <p className="sched-legend">
            <span className="chip warn sched-out" aria-hidden>
              시각
            </span>{" "}
            {OUTSIDE_TEXT}
          </p>
        )}
        <p className="muted sched-cost">
          판단 세션이 많을수록 LLM 비용이 늘어요 → <Link to="/costs">LLM 요금 화면</Link>
        </p>
      </div>

      {dirty && problem && (
        <p className="error" role="alert">
          {problem}
        </p>
      )}

      <div className="btn-row">
        <button type="submit" className="btn primary" disabled={!dirty || busy || !!problem}>
          {save.isPending ? "저장하는 중…" : "저장"}
        </button>
        <button
          type="button"
          className="btn ghost"
          disabled={busy || (!data.custom && !dirty)}
          onClick={toDefault}
          title={
            def ? `기본값: ${def.start}~${def.end} · ${intervalLabel(def.interval_minutes)}마다` : undefined
          }
        >
          {reset.isPending ? "되돌리는 중…" : "기본값으로"}
        </button>
        {def && (
          <small className="muted sched-default" data-testid="sched-default">
            기본값 {def.start}~{def.end} · {intervalLabel(def.interval_minutes)}마다 ({data.tz_label}
            {kst ? "" : ` · 한국 ${kstText(def.start, shift)}~${kstText(def.end, shift)}`})
          </small>
        )}
      </div>

      <div className="llm-status" aria-live="polite">
        {save.isError && <p className="error">{errorText(save.error)}</p>}
        {reset.isError && <p className="error">{errorText(reset.error)}</p>}
      </div>

      {data.next_runs.length > 0 && (
        <div className="sched-next">
          <h4>다음 실행 (한국 시각)</h4>
          <ul aria-label="다음 실행">
            {data.next_runs.slice(0, 6).map((r) => (
              <li key={`${r.name}:${r.at}`}>
                <span>{JOB_KO[r.name] ?? r.name}</span>
                <b>{fmtDateTime(r.at)}</b>
              </li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}
