import { memo, useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, get, post, put } from "../api/client";
import type { NewsImpactLine, NewsItem, NewsRoom, NewsSection } from "../api/types";
import { RelTime } from "../components/RelTime";
import { SkeletonCards } from "../components/Skeleton";
import { readStore, writeStore } from "../market";
import { toast } from "../ui/toast";
import { fmtWhen } from "../utils/format";
import {
  editionLabel,
  IMPACT_OPEN_KEY,
  impactSections,
  NEWS_IDLE_MS,
  NEWS_POLL_MS,
  NEWS_ROOM_KEY,
  safeLink,
  staleError,
  summaryNote,
} from "../utils/newsroom";
import "./newsroom.css";

/**
 * 📰 뉴스 룸 — 세계의 흐름과 대한민국 주요 뉴스 (D-56).
 * 매일 07:00·12:00·17:00(한국 시각)에 서버가 새로 모으고, '지금 새로 모으기'로 직접 모을 수도 있다.
 * 당겨서 새로고침은 서버에 저장된 최신 판을 다시 받는다 (새로 모으지는 않는다)
 */
export function NewsRoomPage() {
  const q = useQuery({
    queryKey: NEWS_ROOM_KEY,
    queryFn: () => get<NewsRoom>("/api/news-room"),
    refetchInterval: (query) => (query.state.data?.refreshing ? NEWS_POLL_MS : NEWS_IDLE_MS),
  });
  const refresh = useRefresh(q.data);
  const settings = useSummaryToggle();

  if (q.isError && !q.data) return <p className="error">뉴스 룸을 불러오지 못했어요: {errorText(q.error)}</p>;
  if (!q.data) return <SkeletonCards heights={[150, 320, 320]} label="뉴스 룸을 불러오는 중" />;

  const room = q.data;
  const ed = room.edition;
  const failed = staleError(room);
  const busy = room.refreshing || refresh.isPending;
  return (
    <div className="stack news-room">
      <section className="card news-head" aria-labelledby="news-title">
        <div className="card-head">
          <h2 id="news-title">뉴스 룸</h2>
          <div className="news-actions">
            {busy && (
              <span className="pill live" role="status" data-testid="news-refreshing">
                뉴스를 모으는 중…
              </span>
            )}
            <button
              type="button"
              className="btn primary news-refresh"
              onClick={() => refresh.mutate()}
              disabled={busy || !room.available}
              title={room.available ? "지금 새로 모으기" : (room.reason ?? undefined)}
            >
              ↻ 지금 새로 모으기
            </button>
          </div>
        </div>
        <p className="muted news-lead">
          세계의 흐름과 대한민국 주요 뉴스를 한곳에 — 매일 {room.schedule.join(" · ")} (한국 시각){" "}
          {room.auto ? "자동 갱신" : "(자동 갱신 꺼짐)"}
        </p>
        <dl className="news-times" data-testid="news-times">
          <div>
            <dt>갱신</dt>
            <dd data-testid="news-updated">
              {room.updated_at ? (
                <>
                  {fmtWhen(room.updated_at)} <span className="muted">·</span>{" "}
                  <RelTime iso={room.updated_at} className="muted" />
                  {ed && <span className="pill">{editionLabel(ed)}</span>}
                </>
              ) : (
                <span className="muted">아직 없음</span>
              )}
            </dd>
          </div>
          <div>
            <dt>다음 갱신</dt>
            <dd data-testid="news-next">
              {room.auto ? fmtWhen(room.next_update_at) : <span className="muted">자동 갱신 꺼짐</span>}
            </dd>
          </div>
          <div>
            <dt>요약</dt>
            <dd>
              <label className="toggle news-summary-toggle">
                <input
                  type="checkbox"
                  checked={room.settings.summary}
                  disabled={settings.isPending}
                  onChange={(e) => settings.mutate(e.target.checked)}
                />
                <span>{summaryNote(room)}</span>
              </label>
            </dd>
          </div>
        </dl>
        {room.reason && (
          <p className="news-alert warn" role="alert" data-testid="news-reason">
            {room.reason}
          </p>
        )}
        {failed && (
          <p className="news-alert warn" role="alert" data-testid="news-failed">
            마지막 갱신에 실패해 이전 판을 보여 줘요: {failed}
          </p>
        )}
        {ed?.headline && (
          <blockquote className="news-headline" data-testid="news-headline">
            <span className="news-headline-tag">오늘의 흐름</span>
            {ed.headline}
          </blockquote>
        )}
        {ed?.summary_error && <p className="muted news-note">{ed.summary_error}</p>}
      </section>

      {ed && <ImpactStrip sections={ed.sections} />}

      {ed ? (
        <div className="news-sections">
          {ed.sections.map((s) => (
            <SectionCard key={s.key} section={s} />
          ))}
        </div>
      ) : (
        <section className="card news-empty" data-testid="news-empty">
          <p>아직 모은 뉴스가 없어요.</p>
          <p className="muted">
            {room.available
              ? `다음 갱신(${fmtWhen(room.next_update_at)})을 기다리거나 '지금 새로 모으기'를 눌러 주세요.`
              : "네이버 뉴스 검색 키를 넣으면 섹션별 뉴스가 여기에 모여요."}
          </p>
          <ul className="news-empty-sections muted">
            {room.sections.map((s) => (
              <li key={s.key}>
                <span aria-hidden>{s.icon}</span> {s.title}
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="muted news-foot">
        기사 출처: 네이버 뉴스 검색 · 제목을 누르면 원문이 새 창으로 열려요. 요약은 기사 제목·요약문만으로
        만든 참고용이며 투자 권유가 아니에요.
      </p>
    </div>
  );
}

/** 지금 새로 모으기: 서버가 백그라운드로 모으는 동안 화면은 1.5초마다 확인하고, 끝나면 토스트 */
function useRefresh(room: NewsRoom | undefined) {
  const qc = useQueryClient();
  const waiting = useRef(false);
  const m = useMutation({
    mutationFn: () => post<NewsRoom>("/api/news-room/refresh"),
    onSuccess: (data) => {
      waiting.current = true;
      qc.setQueryData(NEWS_ROOM_KEY, data);
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const refreshing = room?.refreshing ?? false;
  const failed = room ? staleError(room) : null;
  const articles = room?.edition?.stats.articles;
  useEffect(() => {
    if (!waiting.current || refreshing) return;
    waiting.current = false;
    if (failed) toast.error(`뉴스를 모으지 못했어요: ${failed}`);
    else toast(`뉴스 룸을 새로 고쳤어요${articles != null ? ` (기사 ${articles}건)` : ""}`);
  }, [refreshing, failed, articles]);
  return m;
}

function useSummaryToggle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (summary: boolean) => put<NewsRoom>("/api/news-room/settings", { summary }),
    onSuccess: (data) => {
      qc.setQueryData(NEWS_ROOM_KEY, data);
      toast(data.settings.summary ? "AI 요약을 켰어요 — 다음 갱신부터" : "AI 요약을 껐어요 — 다음 갱신부터");
    },
    onError: (e) => toast.error(errorText(e)),
  });
}

/**
 * 📊 증시 영향 3줄 (D-59): 섹션마다 증시에 영향이 클 것 같은 이슈 3줄과 근거 기사.
 * 판을 만들 때 서버가 함께 저장한 것 — 활성 LLM 이 없거나 실패하면 기사 제목으로 대신하고 'AI 요약 아님'.
 * 휴대폰은 카드를 옆으로 넘겨 본다. 접어 두면 다음에 열어도 접혀 있다
 */
function ImpactStrip({ sections }: { sections: NewsSection[] }) {
  const id = useId();
  const [open, setOpen] = useState(() => readStore(IMPACT_OPEN_KEY) !== "0");
  const cards = impactSections(sections);
  if (cards.length === 0) return null;
  const toggle = () => {
    writeStore(IMPACT_OPEN_KEY, open ? "0" : "1");
    setOpen(!open);
  };
  return (
    <section className="card news-impact" aria-labelledby={`${id}-title`} data-testid="news-impact">
      <div className="news-impact-head">
        <h3 id={`${id}-title`}>증시 영향 큰 뉴스</h3>
        <small className="muted news-impact-sub">섹션별 3줄</small>
        <button
          type="button"
          className="btn tiny news-impact-toggle"
          aria-expanded={open}
          aria-controls={`${id}-cards`}
          onClick={toggle}
        >
          {open ? "접기" : "펼치기"}
        </button>
      </div>
      {open && (
        <>
          {/* 휴대폰: 가로로 넘기는 상자 — 화면 넘기기(탭 이동)보다 이 상자의 가로 스크롤이 먼저 */}
          <ul className="news-impact-cards" id={`${id}-cards`} data-no-swipe>
            {cards.map(({ section, impact }) => (
              <li key={section.key} className="news-impact-card" aria-label={section.title}>
                <div className="news-impact-card-head">
                  <b>
                    <span aria-hidden>{section.icon}</span> {section.title}
                  </b>
                  {impact.source === "ai" ? (
                    <span className="pill news-ai" title="활성 LLM 이 기사 제목·요약문으로 고른 3줄">
                      AI
                    </span>
                  ) : (
                    <span
                      className="pill news-rule"
                      title="LLM 요약이 없어 증시 관련 기사 제목으로 대신했어요"
                    >
                      AI 요약 아님
                    </span>
                  )}
                </div>
                {impact.lines.length === 0 ? (
                  <p className="muted news-impact-none">지난 24시간 기사가 없어요.</p>
                ) : (
                  <ol className="news-impact-lines">
                    {impact.lines.map((ln, i) => (
                      <ImpactLine key={i} line={ln} />
                    ))}
                  </ol>
                )}
              </li>
            ))}
          </ul>
          <small className="muted news-impact-hint" aria-hidden>
            ← 옆으로 넘겨 보기 →
          </small>
        </>
      )}
    </section>
  );
}

function ImpactLine({ line }: { line: NewsImpactLine }) {
  const href = line.link ? safeLink(line.link) : null;
  return (
    <li>
      <span className="news-impact-text">{line.text}</span>
      {href && (
        <a
          className="news-impact-link"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title={line.title ?? undefined}
          aria-label={`관련 기사: ${line.title ?? line.text}`}
        >
          기사 ›
        </a>
      )}
    </li>
  );
}

const SectionCard = memo(function SectionCard({ section: s }: { section: NewsSection }) {
  return (
    <section className={`card news-section s-${s.key}`} aria-labelledby={`news-sec-${s.key}`}>
      <div className="news-section-head">
        <h3 id={`news-sec-${s.key}`}>
          <span aria-hidden>{s.icon}</span> {s.title}
        </h3>
        <span className="muted news-count">{s.items.length}건</span>
      </div>
      {s.summary && <p className="news-summary">{s.summary}</p>}
      {s.items.length === 0 ? (
        <p className="muted news-none">지난 24시간 동안 모인 기사가 없어요.</p>
      ) : (
        <ol className="news-list">
          {s.items.map((it) => (
            <NewsRow key={it.link} item={it} />
          ))}
        </ol>
      )}
      {s.errors.length > 0 && <small className="muted news-errors">{s.errors.join(" · ")}</small>}
    </section>
  );
});

function NewsRow({ item: it }: { item: NewsItem }) {
  const href = safeLink(it.link);
  return (
    <li className="news-item">
      {href ? (
        <a className="news-title" href={href} target="_blank" rel="noopener noreferrer">
          {it.title}
        </a>
      ) : (
        <span className="news-title">{it.title}</span>
      )}
      {it.description && <p className="news-desc">{it.description}</p>}
      <div className="news-meta muted">
        {it.domain && <span>{it.domain}</span>}
        {it.published_at && <RelTime iso={it.published_at} />}
        {it.related > 1 && (
          <span className="pill news-related" title="같은 사건을 다룬 기사 수">
            관련 {it.related}
          </span>
        )}
      </div>
    </li>
  );
}
