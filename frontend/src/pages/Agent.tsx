import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { AgentMemory, AgentStructure, ToolGroup } from "../api/types";
import { Loading } from "../components/Loading";
import { useLiveInterval } from "../live/hooks";
import { useApi, useCurrency, useMarketInfo } from "../market";
import { fmtHm, money } from "../utils/format";
import { isPreOpen } from "../utils/schedule";
import { profileModel } from "../utils/llm";
import { memoKind } from "../utils/memo";

const GROUPS: { key: ToolGroup; label: string; icon: string }[] = [
  { key: "portfolio", label: "내 계좌", icon: "📒" },
  { key: "market", label: "시장·종목", icon: "📈" },
  { key: "research", label: "재무·공시·뉴스", icon: "📰" },
];

const MODE_KO: Record<string, string> = {
  auto: "도구 호출(자동)",
  tools: "도구 호출",
  json: "JSON 응답",
  rules: "규칙 전략(LLM 미설정)",
};

const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

/** 에이전트 구성과 판단 흐름을 실제 설정값으로 보여준다 (/api/agent/structure). */
export function AgentPage() {
  const api = useApi();
  const cur = useCurrency();
  const info = useMarketInfo(api.market);
  // 웹소켓이 연결돼 있으면 30초마다 (세션 시작·끝·설정 변경은 이벤트가 바로 알려 준다), 끊기면 5초마다
  const every = useLiveInterval(api.market, 5000);
  const { data: st, isError } = useQuery({
    queryKey: api.key("agent-structure"),
    queryFn: () => api.get<AgentStructure>("/api/agent/structure"),
    refetchInterval: every,
  });
  if (isError) return <p className="error">에이전트 구성을 불러오지 못했어요.</p>;
  if (!st) return <Loading />;

  const { schedule: sc, risk, paper, llm } = st;
  // 판단 시각 (한국 시각). 장 시작 전인 슬롯만 '장전' — 직접 정한 일정은 첫 슬롯이 장중일 수도 있다
  const times = sc.times ?? [sc.morning, ...sc.sessions].filter(Boolean);
  const openKst = info?.session ? fmtHm(info.session.open) : api.market === "KR" ? "09:00" : null;
  const pre = openKst ? times.filter((t) => isPreOpen(t, openKst)) : sc.times ? [] : [sc.morning];
  const sessions = times.filter((t) => !pre.includes(t));
  const topicsOn = st.memory?.use_topics !== false;
  const llmMode = llm.mode === "rules";
  const terminal = st.tools.find((t) => t.group === "decision");
  const live = st.trading_mode === "live";

  return (
    <div className="agent-page">
      <section className="card agent-hero">
        <div className="agent-hero-id">
          <span className="agent-hero-emoji" aria-hidden>
            {st.persona.emoji}
          </span>
          <div>
            <h2>{st.persona.name} 매니저</h2>
            <p className="muted">
              {llm.model ? profileModel(llm.profile, llm.model) : "LLM 미설정"} ·{" "}
              {MODE_KO[llm.mode] ?? llm.mode}
            </p>
          </div>
        </div>
        <div className="agent-hero-chips">
          {st.agent_running ? (
            <span className="pill live">판단 중…</span>
          ) : (
            <span className="pill">대기</span>
          )}
          {st.sources.map((s) => (
            <span key={s.name} className={`chip ${s.ready ? "ok" : "warn"}`}>
              {s.ready ? "●" : "○"} {s.name}
              {s.note ? ` · ${s.note}` : ""}
            </span>
          ))}
        </div>
      </section>

      <div className="agent-grid">
        <section className="card">
          <div className="card-head">
            <h2>🗺️ 판단 워크플로</h2>
            <span className="muted">세션 한 번이 흘러가는 순서</span>
          </div>
          <ol className="wf" aria-label="판단 워크플로">
            <Step n={1} icon="⏰" title="세션 시작">
              <p>
                {pre.length > 0 && `장전 ${pre.join(", ")} · `}
                {sessions.length > 0 &&
                  `장중 ${
                    sc.interval_minutes > 0
                      ? `${sc.interval_minutes}분마다 (${sessions[0]}~${sessions[sessions.length - 1]}, ${sessions.length}회)`
                      : sessions.join(", ")
                  } · `}
                수동 실행
                {sc.custom && <small className="muted"> (설정에서 정한 일정)</small>}
              </p>
              <p className="muted">거래일에만, 한 번에 하나만 실행해요. 토스 시세가 끊기면 쉬어요.</p>
            </Step>

            <Step n={2} icon="🧠" title="상황 파악">
              <ul className="wf-list">
                <li>운용 정책·페르소나 (설정에서 수정)</li>
                <li>
                  {topicsOn
                    ? "최근 판단 3건과 대화에서 되풀이한 관심사"
                    : "최근 판단 3건 (대화 기억은 꺼 둠)"}
                </li>
                <li>현재 시각과 세션 종류</li>
              </ul>
            </Step>

            <Step
              n={3}
              icon="🔁"
              title={llmMode ? "규칙 전략 계산" : "리서치 루프"}
              badge={llmMode ? undefined : `최대 ${llm.max_steps}단계`}
              active={st.agent_running}
            >
              {llmMode ? (
                <p className="muted">LLM 이 없으면 켜진 규칙 전략(이동평균·RSI) 신호로 결정해요.</p>
              ) : (
                <>
                  <p className="muted">
                    LLM 이 필요한 도구를 골라 부르고, 결과를 읽고, 다시 고르기를 반복해요. 제출을 미루면 한 번
                    재촉하고, 그래도 안 하면 제출을 강제해요.
                  </p>
                  <div className="tool-groups">
                    {GROUPS.map((g) => (
                      <div key={g.key} className="tool-group">
                        <h3>
                          {g.icon} {g.label}
                        </h3>
                        {st.tools
                          .filter((t) => t.group === g.key)
                          .map((t) => (
                            <div
                              key={t.name}
                              className={`tool ${t.ready ? "" : "off"}`}
                              title={t.description}
                            >
                              <code>{t.name}</code>
                              <small>
                                {t.source}
                                {t.ready ? "" : " · 연결 안 됨"}
                              </small>
                            </div>
                          ))}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Step>

            <Step n={4} icon="📝" title="결정 제출" badge={terminal ? terminal.name : undefined}>
              <p>시장 관점 · 요약 · 종목별 매수/매도/유지 (수량 또는 목표 비중, 확신도, 근거)</p>
              <p className="muted">
                종목명은 코드로 바꾸고, 언급하지 않은 보유 종목은 유지로 채워요. 매도부터 실행해요.
              </p>
            </Step>

            <Step n={5} icon="🛡️" title="리스크 검사">
              <div className="rule-grid">
                <Rule label="1회 주문 한도" value={money(risk.max_order_amount, cur)} />
                <Rule label="종목당 최대 비중" value={pct(risk.max_position_weight)} />
                <Rule label="최소 현금 비중" value={pct(risk.min_cash_ratio)} />
                <Rule label="최대 보유 종목" value={`${risk.max_positions}개`} />
                <Rule label="일일 손실 한도" value={pct(risk.daily_loss_limit)} />
                <Rule label="같은 종목 재매수" value={`${risk.min_order_interval_minutes}분 뒤`} />
              </div>
              <p className="muted">
                한도를 넘으면 수량을 줄이거나 막아요. 장 시간 외·중복 주문·킬 스위치는 항상 차단해요.
              </p>
            </Step>

            {live ? (
              <Step n={6} icon="🔴" title="토스증권 실주문">
                <ul className="wf-list">
                  <li>
                    토스증권 실계좌로 실제 주문 (시장가 ·{" "}
                    {cur === "USD"
                      ? "지정가는 $1 이상 0.01·미만 0.0001 달러 단위"
                      : "지정가는 호가단위에 맞춤"}
                    )
                  </li>
                  <li>1분마다 토스 주문 조회로 체결·취소 확인 · 미체결은 당일 장 마감까지 유효</li>
                  {cur === "USD" && <li>장 시작 전 주문은 보내지 않음 (장외 주문을 켠 세션은 예외)</li>}
                  <li>
                    하루 매수 총액 상한 {money(st.live_daily_buy_limit, cur)} ·{" "}
                    {cur === "USD"
                      ? "원화로 환산해 1억원 이상인 주문은 보내지 않음"
                      : "1억원 이상 주문은 보내지 않음"}
                  </li>
                  <li>주문 요청은 자동 재시도하지 않음 (중복 주문 방지)</li>
                </ul>
                <p className="muted">킬 스위치를 누르면 토스에 남은 미체결 주문까지 취소해요.</p>
              </Step>
            ) : (
              <Step n={6} icon="🏦" title="모의 체결">
                <ul className="wf-list">
                  <li>시장가: 토스 현재가 ± 슬리피지 {paper.slippage_bps}bp</li>
                  <li>지정가: 가격에 닿으면 체결 · 장전 주문은 09:00 이후</li>
                  <li>15:20까지 미체결이면 당일 만료</li>
                  <li>
                    수수료 {pct(paper.commission_rate, 3)} · 매도세 {pct(paper.sell_tax_rate, 2)}
                  </li>
                </ul>
                <p className="muted">실제 계좌 주문은 보내지 않아요 (모의투자).</p>
              </Step>
            )}

            <Step n={7} icon="💬" title="기록과 공유" last>
              <p>트레이딩 룸 채팅 · 결정/주문/체결 기록 · 이벤트 로그</p>
              <p className="muted">이번 요약이 다음 세션의 기억이 돼요.</p>
            </Step>
          </ol>
        </section>

        <aside className="agent-side">
          <section className="card">
            <div className="card-head">
              <h2>⚙️ 상시 감시</h2>
            </div>
            <ul className="loop-list">
              <li>
                <b>{sc.monitor_minutes}분마다</b>
                <span>
                  지정가·장전 주문 체결 확인, 손절 {pct(risk.stop_loss)} / 익절 +{pct(risk.take_profit)} 자동
                  매도
                </span>
              </li>
              <li>
                <b>{sc.refresh_minutes}분마다</b>
                <span>보유·관심 종목 시세 갱신, 자산 기록</span>
              </li>
              <li>
                <b>{sc.report}</b>
                <span>마감 리포트</span>
              </li>
              <li>
                <b>언제든</b>
                <span>킬 스위치로 모든 주문 즉시 중단</span>
              </li>
            </ul>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>🗓️ 오늘의 판단 시각</h2>
              <span className="muted">
                {times.length}회{api.market === "KR" ? "" : " · 한국 시각"}
              </span>
            </div>
            <div className="time-chips">
              {times.map((t) =>
                pre.includes(t) ? (
                  <span key={t} className="chip ok">
                    {t} 장전
                  </span>
                ) : (
                  <span key={t} className="chip">
                    {t}
                  </span>
                ),
              )}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>🔌 데이터 출처</h2>
            </div>
            <ul className="loop-list">
              <li>
                <b>토스증권</b>
                <span>시세·일봉·랭킹·수급·섹터·종목 상태 (조회 전용)</span>
              </li>
              <li>
                <b>DART</b>
                <span>재무제표·공시</span>
              </li>
              <li>
                <b>NAVER</b>
                <span>뉴스 검색</span>
              </li>
              {live ? (
                <li>
                  <b>토스 실계좌</b>
                  <span>잔고·주문가능금액·주문·체결 (실제 주문)</span>
                </li>
              ) : (
                <li>
                  <b>모의계좌</b>
                  <span>초기 자금 {money(paper.initial_cash, cur)}</span>
                </li>
              )}
            </ul>
          </section>
        </aside>
      </div>

      {st.memory && <MemoryCard memory={st.memory} />}
    </div>
  );
}

/** 매니저가 판단·답장 때 DB 에서 다시 읽어 넣는 '기억' — 규칙과 지금 들어가는 내용 */
function MemoryCard({ memory }: { memory: AgentMemory }) {
  const { rules = [], sessions = [], topics = [], cards } = memory;
  return (
    <section className="card memory-card" aria-labelledby="memory-title">
      <div className="card-head">
        <h2 id="memory-title">🧠 매니저의 기억</h2>
        <Link to="/memo" className="btn tiny mem-memo-link">
          메모 카드 관리 →
        </Link>
      </div>
      <p className="muted">
        별도 장기 기억 저장소는 없고, 판단·답장 때마다 아래 내용을 DB에서 다시 읽어 넣어요.
      </p>

      {rules.length > 0 && (
        <ul className="mem-rules" aria-label="기억 규칙">
          {rules.map((r) => (
            <li key={r.name}>
              <div className="mem-rule-head">
                <b>{r.name}</b>
                <span className="pill">{r.where}</span>
              </div>
              <small className="muted">{r.detail}</small>
            </li>
          ))}
        </ul>
      )}

      <h3 className="mem-now">지금 기억하고 있는 것</h3>
      {cards && (
        <div className="mem-cards">
          <h4>켜진 메모 카드</h4>
          {cards.length === 0 ? (
            <p className="muted mem-empty">
              켜진 메모 카드가 없어요. <Link to="/memo">메모 카드 만들기 →</Link>
            </p>
          ) : (
            <ul className="mem-card-list" aria-label="켜진 메모 카드">
              {cards.map((c, i) => {
                const k = memoKind(c.kind);
                return (
                  <li key={`${c.kind}:${c.symbol ?? ""}:${i}`}>
                    <span className={`memo-kind k-${c.kind}`}>
                      <span aria-hidden>{k.icon}</span> {k.label}
                    </span>
                    {c.symbol && (
                      <b>
                        {c.name || c.symbol} <small className="muted">{c.symbol}</small>
                      </b>
                    )}
                    {c.text && <span className="mem-card-text">{c.text}</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      <div className="mem-grid">
        <div className="mem-col">
          <h4>최근 판단 세션</h4>
          {sessions.length === 0 ? (
            <p className="muted mem-empty">아직 완료된 판단 세션이 없어요.</p>
          ) : (
            <ul className="mem-list" aria-label="최근 판단 세션">
              {sessions.map((x) => (
                <li key={x.run_id}>
                  <div className="mem-meta">
                    <b>세션 #{x.run_id}</b>
                    <small className="muted">{x.at}</small>
                  </div>
                  <p>{x.summary || "(요약 없음)"}</p>
                  <small className="muted">매매: {x.actions}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="mem-col">
          <h4>대화에서 되풀이한 관심사</h4>
          {/* 메모 화면 '대화에서 기억하기'에서 끄면 매니저는 주제를 받지 않는다 */}
          {memory.use_topics === false ? (
            <p className="muted mem-note" data-testid="topics-off">
              대화 기억은 꺼 두었어요 — 매니저가 판단·답장 때 받지 않아요.{" "}
              <Link to="/memo">대화에서 기억하기 →</Link>
            </p>
          ) : (
            <p className="muted mem-note">
              여러 날 되풀이해 물은 주제는 오래, 한 번 물은 주제는 그날만 들어가요.{" "}
              <Link to="/memo">주제 보기·고정 →</Link>
            </p>
          )}
          {topics.length === 0 ? (
            <p className="muted mem-empty">지금 기억에 들어가는 주제가 없어요.</p>
          ) : (
            <ul className="mem-list" aria-label="대화에서 되풀이한 관심사">
              {topics.map((t) => (
                <li key={t.id}>
                  <div className="mem-meta">
                    <b>{t.label}</b>{" "}
                    <small className="muted">
                      중요도 {Math.round(t.score * 100)} · {t.days}일 언급
                      {t.pin === "pin" ? " · 늘 기억" : ""}
                    </small>
                  </div>
                  {t.quotes.length > 0 && <p>{t.quotes[t.quotes.length - 1].text}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function Step({
  n,
  icon,
  title,
  badge,
  active,
  last,
  children,
}: {
  n: number;
  icon: string;
  title: string;
  badge?: string;
  active?: boolean;
  last?: boolean;
  children: ReactNode;
}) {
  return (
    <li className={`wf-step ${active ? "active" : ""} ${last ? "last" : ""}`}>
      <div className="wf-dot" aria-hidden>
        {icon}
      </div>
      <div className="wf-body">
        <h3>
          <span className="wf-n">{n}</span> {title}
          {badge && <span className="pill">{badge}</span>}
          {active && <span className="pill live">진행 중</span>}
        </h3>
        {children}
      </div>
    </li>
  );
}

function Rule({ label, value }: { label: string; value: string }) {
  return (
    <div className="rule">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
