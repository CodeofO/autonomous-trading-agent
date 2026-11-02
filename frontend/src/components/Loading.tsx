/** 데이터를 받는 중. 넓은 화면은 '불러오는 중…' 글자, 휴대폰은 카드 모양 자리 표시(레이아웃이 덜 튄다) */
export function Loading({ cards = 3 }: { cards?: number }) {
  return (
    <div className="loading" aria-busy="true">
      <p className="muted loading-text">불러오는 중…</p>
      <div className="skeleton" aria-hidden>
        {Array.from({ length: cards }, (_, i) => (
          <i key={i} />
        ))}
      </div>
    </div>
  );
}
