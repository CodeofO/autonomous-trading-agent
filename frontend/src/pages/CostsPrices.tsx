import type { LlmPrice } from "../api/types";
import { fmtTokens } from "../utils/format";
import { providerLabel } from "../utils/costs";

/** 단가표의 모델이 지금 쓰는 모델인지 ('openai/gpt-6.1-sol'·'gpt-6.1-sol-2026-10-01' 도 같은 모델로) */
function isCurrentPrice(p: LlmPrice, current: string | null): boolean {
  if (!current) return false;
  const tail = current.split("/").pop() ?? current;
  return tail === p.id || (tail.startsWith(`${p.id}-`) && /^\d/.test(tail.slice(p.id.length + 1)));
}

/**
 * 주요 모델 단가 (100만 토큰당) — 모델끼리 한눈에 비교하는 표. 지금 쓰는 모델은 강조한다.
 * 출력 칸 막대 = 가장 비싼 출력 단가 대비 (요금의 대부분이 출력·입력이라 비교 기준으로 쓴다).
 * 예상 요금은 계산하지 않는다(실제 요금은 위 장부) — 단가만.
 */
export function PriceTable({
  prices,
  current,
  price,
}: {
  prices: LlmPrice[];
  current: string | null;
  /** 100만 토큰당 USD → 화면 글자 (원/$ 전환을 따른다) */
  price: (usd: number) => string;
}) {
  if (prices.length === 0) return null;
  const maxOut = Math.max(...prices.map((p) => p.output));
  const maxIn = Math.max(...prices.map((p) => p.input));
  return (
    <section className="card costs-prices" aria-label="주요 모델 단가">
      <div className="card-head">
        <h2>주요 모델 단가</h2>
        <span className="muted">100만 토큰당</span>
      </div>
      <div className="table-scroll">
        <table className="table costs-price-table">
          <thead>
            <tr>
              <th scope="col">모델</th>
              <th scope="col" className="num">
                입력
              </th>
              <th scope="col" className="num">
                캐시 읽기
              </th>
              <th scope="col" className="num">
                출력
              </th>
            </tr>
          </thead>
          <tbody>
            {prices.map((p) => {
              const now = isCurrentPrice(p, current);
              return (
                <tr key={p.id} className={now ? "now" : undefined} aria-current={now ? "true" : undefined}>
                  <th scope="row">
                    <div className="costs-price-model">
                      <span className="costs-price-name">
                        <b>{p.name}</b>
                        {now && <span className="pill paid">지금</span>}
                      </span>
                      <small className="muted">
                        {providerLabel(p.provider)} · {p.tier}
                      </small>
                      <PriceNotes p={p} price={price} />
                    </div>
                  </th>
                  <td className="num">
                    {price(p.input)}
                    <Bar value={p.input} max={maxIn} />
                  </td>
                  <td className="num">{price(p.cached)}</td>
                  <td className="num">
                    {price(p.output)}
                    <Bar value={p.output} max={maxOut} />
                  </td>
                </tr>
              );
            })}
            <tr className="free">
              <th scope="row">
                <div className="costs-price-model">
                  <span className="costs-price-name">
                    <b>자체 서버 모델</b>
                  </span>
                  <small className="muted">무료 묶음 (LLM_TEST_*)</small>
                </div>
              </th>
              <td className="num">0</td>
              <td className="num">0</td>
              <td className="num">0</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="muted costs-price-foot">
        추론 토큰은 출력 단가로 계산돼요. OpenAI 는 캐시에 새로 넣는 입력을 입력 단가의 1.25배(30분 캐시)로
        받아요. 실제로 쓴 요금은 위 장부 숫자예요.
      </p>
    </section>
  );
}

function PriceNotes({ p, price }: { p: LlmPrice; price: (usd: number) => string }) {
  const notes: string[] = [];
  if (p.cache_write != null) notes.push(`캐시 쓰기 ${price(p.cache_write)}`);
  if (p.long && p.long_from)
    notes.push(
      `입력 ${fmtTokens(p.long_from)} 초과 호출은 입력 ${price(p.long.input)} · 출력 ${price(p.long.output)}`,
    );
  for (const t of p.tiers ?? []) {
    notes.push(`입력 ${fmtTokens(t.from)} 초과 호출은 입력 ${price(t.input)} · 출력 ${price(t.output)}`);
  }
  if (p.next) {
    const [, m, d] = p.next.since.split("-").map(Number);
    notes.push(`${m}월 ${d}일부터 입력 ${price(p.next.input)} · 출력 ${price(p.next.output)}`);
  }
  if (notes.length === 0) return null;
  return (
    <ul className="costs-price-notes">
      {notes.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  );
}

function Bar({ value, max }: { value: number; max: number }) {
  if (!(max > 0)) return null;
  const pct = Math.max(2, Math.round((value / max) * 100));
  return (
    <span className="costs-price-bar" aria-hidden>
      <i style={{ width: `${pct}%` }} />
    </span>
  );
}
