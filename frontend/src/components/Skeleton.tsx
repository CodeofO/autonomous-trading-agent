/*
 * 데이터를 받는 동안의 자리 표시 (넓은 화면·휴대폰 모두 보인다).
 * 받은 내용이 이 자리를 통째로 바꾸므로 그 사이 다른 내용이 밀리지 않는다.
 * (다음 단계에서 화면 전체 스켈레톤 체계로 바꿀 자리 — 지금은 화면 밀림을 막는 최소한)
 */

/** 줄 몇 개짜리 자리 */
export function SkeletonLines({ lines = 3, label }: { lines?: number; label?: string }) {
  return (
    <div className="skel-lines" aria-busy="true" aria-label={label} role={label ? "status" : undefined}>
      {Array.from({ length: lines }, (_, i) => (
        <i key={i} />
      ))}
    </div>
  );
}

/** 카드 여러 장짜리 자리 (화면 하나를 통째로 기다릴 때) */
export function SkeletonCards({ heights, label }: { heights: number[]; label: string }) {
  return (
    <div className="stack skel-page" aria-busy="true" aria-label={label} role="status">
      {heights.map((h, i) => (
        <section key={i} className="card skel-card" style={{ minHeight: h }} aria-hidden>
          <i className="skel-title" />
          <SkeletonLines lines={Math.max(2, Math.min(6, Math.round(h / 60)))} />
        </section>
      ))}
    </div>
  );
}
