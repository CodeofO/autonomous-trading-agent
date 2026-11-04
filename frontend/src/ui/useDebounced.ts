import { useEffect, useState } from "react";

/** 값이 ms 동안 그대로면 그 값 (검색어를 다 친 뒤 찾기) */
export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}
