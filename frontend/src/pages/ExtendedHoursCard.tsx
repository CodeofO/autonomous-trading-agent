import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, put } from "../api/client";
import type { ExtendedHoursInfo, ExtendedHoursUpdate, MarketCode } from "../api/types";
import { invalidateNames, MARKET_META, useMarket } from "../market";
import { toast } from "../ui/toast";
import { EXTENDED_API, extendedHoursKey, extendedHoursQuery } from "./settingsQueries";
import { sessionTimeText } from "../utils/extended";
import { MARKET_TABS } from "../utils/schedule";

/** 🌙 장외 주문 — 프리마켓·애프터마켓(미장은 데이마켓도)에서 주문을 받을지 시장마다 켜고 끈다 (D-57) */
export function ExtendedHoursCard() {
  const kr = useQuery(extendedHoursQuery("KR"));
  const us = useQuery(extendedHoursQuery("US"));
  const { market: viewing } = useMarket();
  const [market, setMarket] = useState<MarketCode>(viewing);
  // 예전 서버(이 기능이 없음)면 카드를 그리지 않는다
  if (kr.isError && !kr.data) return null;
  const usVisible = !!us.data;
  const current: MarketCode = market === "US" && !usVisible ? "KR" : market;
  const data = current === "KR" ? kr.data : us.data;
  return (
    <section className="card ext-card" aria-labelledby="ext-title">
      <div className="card-head">
        <h2 id="ext-title">🌙 장외 주문</h2>
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
        켠 세션에서만 주문을 받아요. 시각은 토스 장 운영 정보를 날마다 받아 그대로 써서{" "}
        {current === "US" ? "서머타임·휴장·조기 마감이" : "NXT 휴장·단축 운영이"} 자동으로 반영돼요.
      </p>
      {data ? (
        <ExtendedEditor key={current} market={current} data={data} />
      ) : (
        <p className="muted">불러오는 중…</p>
      )}
    </section>
  );
}

function ExtendedEditor({ market, data }: { market: MarketCode; data: ExtendedHoursInfo }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (body: ExtendedHoursUpdate) => put<ExtendedHoursInfo>(EXTENDED_API[market], body),
    onSuccess: (d, body) => {
      qc.setQueryData(extendedHoursKey(market), d);
      // 주문 안내(상단·포트폴리오)와 판단 일정의 '장 시간 밖' 표시가 바뀐다
      invalidateNames(qc, ["system", "portfolio", "schedule", "markets", "overview"], market);
      const [key, on] = Object.entries(body)[0] ?? [];
      const label = d.sessions.find((s) => s.key === key)?.label ?? "장외";
      const m = MARKET_META[market];
      toast(`${m.flag} ${m.name} ${label} 주문을 ${on ? "켰어요" : "껐어요"} ✓`);
    },
  });
  const anyOn = data.sessions.some((s) => s.enabled);
  return (
    <>
      <ul className="ext-list">
        {data.sessions.map((s) => (
          <li key={s.key} className="ext-row">
            <label className="toggle ext-toggle">
              <span className="ext-text">
                <b>
                  {s.label}
                  {s.active && <span className="pill ext-now">진행 중</span>}
                </b>
                <small className="muted">{s.hint}</small>
                <small className="num ext-time">{sessionTimeText(s, market)}</small>
              </span>
              <input
                type="checkbox"
                className="switch"
                checked={s.enabled}
                disabled={save.isPending}
                aria-label={`${s.label} 주문`}
                onChange={(e) => save.mutate({ [s.key]: e.target.checked })}
              />
            </label>
          </li>
        ))}
      </ul>
      {!data.calendar_known && (
        <p className="muted ext-note">
          ⚠️ 아직 토스 장 운영 정보를 받지 못했어요. 받기 전에는 켜 두어도 장외 주문을 받지 않아요.
        </p>
      )}
      {anyOn && data.live && (
        <p className="error ext-note">
          🔴 실거래 계좌예요 — 켠 장외 세션에서도 토스증권으로 실제 주문이 나가요. 장외는 거래가 적어 가격이
          크게 움직일 수 있어요.
        </p>
      )}
      {anyOn && !data.live && (
        <p className="muted ext-note">
          모의계좌는 그 세션에 실제 장외 거래가 있을 때(시세 시각이 세션 안일 때)만 현재가로 체결하고, 세션이
          끝나면 미체결분을 취소해요.
        </p>
      )}
      <p className="muted ext-note">지금 주문 접수: {data.order_hours}</p>
      {save.isError && <p className="error">저장하지 못했어요: {errorText(save.error)}</p>}
    </>
  );
}
