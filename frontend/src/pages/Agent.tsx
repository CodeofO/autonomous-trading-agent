import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "../api/client";
import type { AgentStructure, ToolGroup } from "../api/types";
import { fmtWon } from "../utils/format";

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
  const { data: st, isError } = useQuery({
    queryKey: ["agent-structure"],
    queryFn: () => get<AgentStructure>("/api/agent/structure"),
    refetchInterval: 5000,
  });
  if (isError) return <p className="error">에이전트 구성을 불러오지 못했어요.</p>;
  if (!st) return <p className="muted">불러오는 중…</p>;

  const { schedule: sc, risk, paper, llm } = st;
  const sessions = sc.sessions;
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
              {llm.model ?? "LLM 미설정"} · {MODE_KO[llm.mode] ?? llm.mode}
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
                장전 {sc.morning} · 장중{" "}
                {sc.interval_minutes > 0
                  ? `${sc.interval_minutes}분마다 (${sessions[0]}~${sessions[sessions.length - 1]}, ${sessions.length}회)`
                  : sessions.join(", ")}{" "}
                · 수동 실행
              </p>
              <p className="muted">거래일에만, 한 번에 하나만 실행해요. 토스 시세가 끊기면 쉬어요.</p>
            </Step>

            <Step n={2} icon="🧠" title="상황 파악">
              <ul className="wf-list">
                <li>운용 정책·페르소나 (설정에서 수정)</li>
                <li>최근 판단 3건과 투자자 메모 5건</li>
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
                <Rule label="1회 주문 한도" value={fmtWon(risk.max_order_amount)} />
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
                  <li>토스증권 실계좌로 실제 주문 (시장가 · 지정가는 호가단위에 맞춤)</li>
                  <li>1분마다 토스 주문 조회로 체결·취소 확인 · 미체결은 당일 장 마감까지 유효</li>
                  <li>
                    하루 매수 총액 상한 {fmtWon(st.live_daily_buy_limit)} · 1억원 이상 주문은 보내지 않음
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
              <span className="muted">{sessions.length + 1}회</span>
            </div>
            <div className="time-chips">
              <span className="chip ok">{sc.morning} 장전</span>
              {sessions.map((t) => (
                <span key={t} className="chip">
                  {t}
                </span>
              ))}
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
                  <span>초기 자금 {fmtWon(paper.initial_cash)}</span>
                </li>
              )}
            </ul>
          </section>
        </aside>
      </div>
    </div>
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
