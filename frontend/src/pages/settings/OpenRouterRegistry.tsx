import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, errorText, get, post } from "../../api/client";
import type { LlmModelOption, LlmProfiles, OpenRouterCatalog } from "../../api/types";
import { toast } from "../../ui/toast";
import { contextText, modelPriceText } from "../../utils/llm";
import "./llmProviders.css";

const SEARCH_DEBOUNCE_MS = 300;

/**
 * OpenRouter 모델 등록 (D-71) — OpenRouter 모델 수백 개 중 쓸 것만 등록해 두면 '유료 모델' 목록에 나온다.
 * 검색은 OpenRouter 목록 API(도구 호출 지원 모델만, Anthropic 제외)에서. 지금 쓰는 모델·.env 모델은 뺄 수 없다.
 */
export function OpenRouterRegistry({
  registered,
  models,
  envModel,
}: {
  registered: string[];
  models: LlmModelOption[];
  envModel: string | null;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [q]);

  const search = useQuery({
    queryKey: ["openrouter-catalog", query],
    queryFn: () =>
      get<OpenRouterCatalog>(`/api/llm/openrouter/catalog?q=${encodeURIComponent(query)}&limit=30`),
    enabled: query.length > 0,
    staleTime: 60_000,
  });
  const applied = (data: LlmProfiles) => {
    qc.setQueryData(["llm-profile"], data);
    void qc.invalidateQueries({ queryKey: ["openrouter-catalog"] });
  };
  const add = useMutation({
    mutationFn: (id: string) => post<LlmProfiles>("/api/llm/openrouter/registered", { id }),
    onSuccess: (data, id) => {
      applied(data);
      toast(`OpenRouter 모델을 등록했어요 (${id}) 위 '유료 모델'에서 고를 수 있어요.`);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      del<LlmProfiles>(`/api/llm/openrouter/registered?id=${encodeURIComponent(id)}`),
    onSuccess: (data, id) => {
      applied(data);
      toast(`등록을 뺐어요 (${id})`);
    },
  });

  const byId = new Map(models.filter((m) => m.provider === "openrouter").map((m) => [m.id, m]));
  const items = search.data?.items ?? [];
  const error = add.error ?? remove.error;

  return (
    <section className="or-registry" aria-label="OpenRouter 모델 등록">
      <h4>OpenRouter 모델 등록</h4>
      <p className="muted">
        등록한 모델만 위 &lsquo;유료 모델&rsquo;에 나와요. 매니저는 도구 호출을 쓰므로 도구 호출을 지원하는
        모델만 찾아져요.
      </p>
      <ul className="or-registered">
        {registered.map((id) => {
          const m = byId.get(id);
          const locked = m?.selected || id === envModel;
          return (
            <li key={id}>
              <span className="or-name">
                <b>{m?.name ?? id}</b>
                <small className="muted">
                  {id}
                  {m?.selected ? " · 지금 사용 중" : id === envModel ? " · .env 기본" : ""}
                </small>
                <small className="muted">
                  {(m && modelPriceText(m)) ?? "가격 정보 없음 (목록을 받으면 나와요)"}
                  {m?.context_length ? ` · ${contextText(m.context_length)}` : ""}
                </small>
              </span>
              <button
                type="button"
                className="btn ghost"
                disabled={locked || remove.isPending}
                title={locked ? "지금 쓰는 모델·.env 모델은 뺄 수 없어요" : undefined}
                onClick={() => remove.mutate(id)}
              >
                빼기
              </button>
            </li>
          );
        })}
      </ul>
      <input
        type="search"
        className="or-search"
        aria-label="OpenRouter 모델 검색"
        placeholder="모델 검색 (예: deepseek, qwen, gpt)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {query && (
        <div className="or-results" aria-live="polite">
          {search.isPending ? (
            <small className="muted">찾는 중…</small>
          ) : search.isError ? (
            <p className="error">검색하지 못했어요: {errorText(search.error)}</p>
          ) : items.length === 0 ? (
            <small className="muted">
              {search.data?.error ? `목록을 받지 못했어요: ${search.data.error}` : "맞는 모델이 없어요"}
            </small>
          ) : (
            <ul>
              {items.map((m) => (
                <li key={m.id}>
                  <span className="or-name">
                    <b>{m.name}</b>
                    <small className="muted">
                      {m.id} · {modelPriceText(m) ?? "가격 정보 없음"}
                      {m.context_length ? ` · ${contextText(m.context_length)}` : ""}
                    </small>
                  </span>
                  <button
                    type="button"
                    className="btn"
                    disabled={m.registered || add.isPending}
                    onClick={() => add.mutate(m.id)}
                  >
                    {m.registered ? "등록됨" : "등록"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <p className="error">{errorText(error)}</p>}
    </section>
  );
}
