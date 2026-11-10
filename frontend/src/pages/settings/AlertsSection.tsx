import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, errorText, post, put } from "../../api/client";
import type { PushPrefs, PushStatus, PushSubscribeResult, PushTestResult } from "../../api/types";
import { RelTime } from "../../components/RelTime";
import {
  currentSubscription,
  deviceLabel,
  getDeviceId,
  notificationPermission,
  pushErrorText,
  pushSupport,
  readEnv,
  requestPermission,
  setDeviceId,
  subscribePush,
  unsubscribePush,
} from "../../push";
import { toast } from "../../ui/toast";
import { fmtDateTime } from "../../utils/format";
import { pushQuery } from "../settingsQueries";
import { SectionGate } from "./SectionGate";

/** 🔔 알림 — 이 기기 휴대폰 알림, 받을 알림 종류, 알림 받는 기기 */
export function AlertsSection() {
  const ready = !useQuery(pushQuery).isPending;
  return (
    <SectionGate ready={ready} heights={[340]} label="알림 설정 불러오는 중">
      <PushSettings />
    </SectionGate>
  );
}

const PUSH_PREFS: { key: keyof PushPrefs; label: string; hint: string }[] = [
  { key: "fills", label: "체결", hint: "매수·매도 주문이 체결되면" },
  { key: "risk", label: "리스크 차단", hint: "리스크 규칙이 주문을 막으면" },
  { key: "alerts", label: "킬 스위치·오류", hint: "엔진 정지, 연결·실행 오류" },
  { key: "agent", label: "에이전트 판단 완료·리포트", hint: "매 판단 세션·리포트마다 (자주 올 수 있어요)" },
];

function testResultText(r: PushTestResult): string {
  const extra = [r.failed ? `실패 ${r.failed}` : "", r.removed ? `만료된 기기 ${r.removed}개 정리` : ""]
    .filter(Boolean)
    .join(" · ");
  const head = r.sent > 0 ? `보냈어요 ✓ (${r.sent}대)` : "보내지 못했어요";
  return extra ? `${head} · ${extra}` : head;
}

/** 휴대폰 알림(웹 푸시). 이 기기 켜기/끄기, 받을 알림 종류, 등록된 기기 목록 */
export function PushSettings() {
  const qc = useQueryClient();
  const q = useQuery(pushQuery);
  const [env] = useState(readEnv);
  const support = pushSupport(env);
  const [permission, setPermission] = useState(notificationPermission);
  // 이 기기 브라우저의 구독 주소 (undefined = 확인 중)
  const [endpoint, setEndpoint] = useState<string | null | undefined>(support === "ok" ? undefined : null);
  const [myId, setMyId] = useState(getDeviceId);
  useEffect(() => {
    if (support !== "ok") return;
    let alive = true;
    currentSubscription().then((s) => alive && setEndpoint(s?.endpoint ?? null));
    return () => {
      alive = false;
    };
  }, [support]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["push"] });
  const remember = (id: string | null) => {
    setDeviceId(id);
    setMyId(id);
  };

  const enable = useMutation({
    // 권한 요청은 클릭 처리 안에서 이미 시작해 넘겨받는다 (iOS 는 사용자 터치 직후에만 권한 창을 띄운다)
    mutationFn: async (asked: Promise<NotificationPermission>) => {
      const perm = await asked;
      setPermission(perm);
      if (perm === "denied") return null; // 아래 '권한 꺼짐' 안내로 바뀐다
      if (perm !== "granted") throw new Error("알림 권한을 허용해야 켤 수 있어요");
      const body = await subscribePush(q.data?.public_key ?? "");
      setEndpoint(body.endpoint);
      const label = deviceLabel(navigator.userAgent, navigator.platform, navigator.maxTouchPoints);
      const r = await post<PushSubscribeResult>("/api/push/subscribe", { ...body, label });
      return r.id;
    },
    onSuccess: (id) => {
      if (id) remember(id);
      refresh();
      if (id) toast("이 기기에서 알림을 켰어요 🔔");
    },
  });
  const disable = useMutation({
    mutationFn: async () => {
      // 이 기기 구독부터 끊는다 — 서버 요청이 실패해도 알림은 더 오지 않는다
      const ep = (await unsubscribePush()) ?? endpoint;
      setEndpoint(null);
      remember(null);
      if (ep) await post("/api/push/unsubscribe", { endpoint: ep });
    },
    onSuccess: () => toast("이 기기 알림을 껐어요"),
    onSettled: refresh,
  });
  const test = useMutation({
    mutationFn: (ep: string | null) => post<PushTestResult>("/api/push/test", ep ? { endpoint: ep } : {}),
    onSuccess: (r) => {
      if (r.removed) refresh();
      // 보냈으면 토스트로, 한 대도 못 보냈으면 그 자리에 남긴다 (아래)
      if (r.sent > 0) toast(testResultText(r));
    },
  });
  const savePrefs = useMutation({
    mutationFn: (prefs: PushPrefs) => put<PushPrefs>("/api/push/prefs", prefs),
    onSuccess: (prefs) => {
      qc.setQueryData<PushStatus>(["push"], (old) => old && { ...old, prefs });
      toast("받을 알림을 저장했어요 ✓");
    },
  });
  const remove = useMutation({
    mutationFn: async (id: string) => {
      await del(`/api/push/devices/${encodeURIComponent(id)}`);
      if (id === myId) {
        await unsubscribePush().catch(() => null);
        setEndpoint(null);
        remember(null);
      }
    },
    onSuccess: () => {
      refresh();
      toast("기기를 목록에서 뺐어요");
    },
  });

  const data = q.data;
  const devices = data?.devices ?? [];
  const isOn = !!endpoint && !!myId && devices.some((d) => d.id === myId);
  const prefs = savePrefs.isPending ? savePrefs.variables : data?.prefs;
  const busy = enable.isPending || disable.isPending;

  let state: ReactNode = null;
  if (support === "insecure") {
    state = (
      <p className="push-note warn">
        알림은 보안 연결(HTTPS) 주소에서만 켤 수 있어요. 지금 주소는 HTTPS 가 아니에요 — Tailscale 의 HTTPS
        주소(https:// 로 시작)로 접속해서 켜 주세요.
      </p>
    );
  } else if (support === "ios-install") {
    state = (
      <p className="push-note warn">
        iPhone 은 홈 화면에 추가한 앱에서만 알림을 받을 수 있어요. Safari 의 <b>공유 버튼</b> →{" "}
        <b>'홈 화면에 추가'</b>를 누른 뒤, 홈 화면의 아이콘으로 열어서 여기서 켜 주세요. (iOS 16.4 이상)
      </p>
    );
  } else if (support === "unsupported") {
    state = <p className="push-note warn">이 브라우저는 알림을 지원하지 않아요.</p>;
  } else if (permission === "denied") {
    state = (
      <p className="push-note warn">
        {env.ios
          ? "알림 권한이 꺼져 있어요. iPhone 설정 > 알림 > AI 펀드매니저에서 알림을 허용한 뒤 앱을 다시 열어 주세요."
          : "알림 권한이 꺼져 있어요. 브라우저의 사이트 설정 > 알림에서 허용한 뒤 새로고침해 주세요."}
      </p>
    );
  } else if (endpoint === undefined) {
    state = <p className="muted">이 기기 상태 확인 중…</p>;
  } else if (isOn) {
    state = (
      <>
        <p className="ok">이 기기에서 알림을 받고 있어요 ✓</p>
        <div className="btn-row push-actions">
          <button
            className="btn primary"
            disabled={test.isPending}
            onClick={() => test.mutate(endpoint ?? null)}
          >
            테스트 알림 보내기
          </button>
          <button className="btn ghost" disabled={busy} onClick={() => disable.mutate()}>
            이 기기 알림 끄기
          </button>
        </div>
      </>
    );
  } else {
    state = (
      <>
        <p className="muted">이 기기는 아직 알림을 받지 않아요.</p>
        <div className="btn-row push-actions">
          <button
            className="btn primary"
            disabled={busy || !data?.public_key}
            onClick={() => enable.mutate(requestPermission())}
          >
            {enable.isPending ? "켜는 중…" : "알림 켜기"}
          </button>
        </div>
      </>
    );
  }

  return (
    <section className="card push-card">
      <h2>휴대폰 알림</h2>
      <p className="muted">체결·리스크 차단·킬 스위치 같은 중요한 일을 휴대폰 알림으로 바로 알려 드려요.</p>
      {q.isLoading && <p className="muted">불러오는 중…</p>}
      {q.isError && <p className="error">{errorText(q.error)}</p>}
      {data && !data.available && (
        <p className="push-note warn">
          서버에서 알림을 보낼 수 없어요: {data.reason ?? "서버 설정을 확인해 주세요"}
        </p>
      )}
      {data?.available && (
        <>
          <div className="push-state">
            {state}
            {enable.isError && <p className="error">{pushErrorText(enable.error)}</p>}
            {disable.isError && <p className="error">{pushErrorText(disable.error)}</p>}
            {test.isSuccess && test.data.sent === 0 && <p className="error">{testResultText(test.data)}</p>}
            {test.isError && <p className="error">{errorText(test.error)}</p>}
          </div>

          <div className="push-prefs" role="group" aria-label="받을 알림">
            <h3>받을 알림</h3>
            {PUSH_PREFS.map((p) => (
              <label key={p.key} className="toggle">
                <input
                  type="checkbox"
                  checked={prefs?.[p.key] ?? false}
                  disabled={!prefs}
                  onChange={(e) => prefs && savePrefs.mutate({ ...prefs, [p.key]: e.target.checked })}
                />
                <span>
                  {p.label} <small className="muted">{p.hint}</small>
                </span>
              </label>
            ))}
            {savePrefs.isError && <p className="error">{errorText(savePrefs.error)}</p>}
          </div>

          {devices.length > 0 && (
            <div className="push-devices-wrap">
              <div className="card-head">
                <h3>알림 받는 기기 {devices.length}대</h3>
                {!isOn && (
                  <button className="btn tiny" disabled={test.isPending} onClick={() => test.mutate(null)}>
                    모든 기기에 테스트
                  </button>
                )}
              </div>
              <ul className="watch push-devices">
                {devices.map((d) => (
                  <li key={d.id}>
                    <span className="push-device">
                      <span>
                        {d.label || "이름 없는 기기"}
                        {d.id === myId && isOn && <span className="pill">이 기기</span>}
                      </span>
                      <small className="muted">
                        등록 {fmtDateTime(d.created_at)} · 마지막 전송{" "}
                        {d.last_ok ? <RelTime iso={d.last_ok} /> : "아직 없음"}
                      </small>
                    </span>
                    <button
                      className="btn tiny ghost"
                      aria-label={`${d.label || "기기"} 삭제`}
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(d.id)}
                    >
                      삭제
                    </button>
                  </li>
                ))}
              </ul>
              {remove.isError && <p className="error">{errorText(remove.error)}</p>}
            </div>
          )}
        </>
      )}
    </section>
  );
}
