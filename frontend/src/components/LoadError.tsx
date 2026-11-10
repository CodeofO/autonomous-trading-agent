import { errorText } from "../api/client";
import { Icon } from "./Icon";

interface Q {
  isError: boolean;
  data: unknown;
  error: unknown;
  refetch: () => unknown;
}

/** 처음부터 받지 못한(데이터가 없는) 조회 — 빈 표와 구분되도록 오류와 '다시 시도'를 보여 준다 */
export function failedOf(queries: Q[]): Q[] {
  return queries.filter((q) => q.isError && q.data === undefined);
}

export function LoadError({ what, queries }: { what: string; queries: Q[] }) {
  const failed = failedOf(queries);
  if (!failed.length) return null;
  return (
    <section className="card load-error" role="alert">
      <div>
        <b>
          <Icon name="alert" size={16} /> {what} 불러오지 못했어요
        </b>
        <p className="muted">{errorText(failed[0].error)}</p>
      </div>
      <button className="btn" onClick={() => failed.forEach((q) => void q.refetch())}>
        다시 시도
      </button>
    </section>
  );
}
