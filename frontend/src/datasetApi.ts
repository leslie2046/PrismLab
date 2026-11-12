import type { BulkResult, SelectionSnapshot } from './bulkSelection';
import { authenticatedFetch } from './api';
import type { Catalog, Dataset, DatasetItem, DatasetSettings, DatasetGenerationOptions, DatasetSources, LlmSelection, GenerationDetails, GenerationRequest, GenerationRequestSummary } from './types';
import type { PageResult } from './pagination';
import { providerDisplayName } from './providerPresets';
import type { Language } from './i18n';

async function call<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const multipart = body instanceof FormData;
  const response = await authenticatedFetch(`/api/${path}`, { method, signal,
    ...(body === undefined ? {} : { headers: multipart ? {} : { 'Content-Type': 'application/json' }, body: multipart ? body : JSON.stringify(body) }),
  });
  if (!response.ok) {
    const value = await response.json().catch(() => null);
    throw new Error(typeof value?.detail === 'string' ? value.detail : `Request failed (${response.status})`);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const datasetApi = {
  list: (page: number, size: number, q: string, signal?: AbortSignal, purpose = '') => call<PageResult<Dataset>>(`datasets?${new URLSearchParams({ page: String(page), page_size: String(size), q, ...(purpose ? {purpose} : {}) })}`, 'GET', undefined, signal),
  get: (id: string, signal?: AbortSignal) => call<Dataset>(`datasets/${id}`, 'GET', undefined, signal),
  items: (id: string, page: number, size: number, q: string, signal?: AbortSignal) => call<PageResult<DatasetItem>>(`datasets/${id}/items?${new URLSearchParams({ page: String(page), page_size: String(size), q })}`, 'GET', undefined, signal),
  generation: (id: string, itemId: string, signal?: AbortSignal) => call<GenerationDetails>(`datasets/${id}/items/${itemId}/generation`, 'GET', undefined, signal),
  generationRequests: (id: string, itemId: string, page: number, size: number, signal?: AbortSignal) => call<PageResult<GenerationRequestSummary>>(`datasets/${id}/items/${itemId}/generation/requests?page=${page}&page_size=${size}`, 'GET', undefined, signal),
  generationRequest: (id: string, itemId: string, requestId: string, signal?: AbortSignal) => call<GenerationRequest>(`datasets/${id}/items/${itemId}/generation/requests/${requestId}`, 'GET', undefined, signal),
  itemSelection: (id: string, q: string) => call<SelectionSnapshot>(`datasets/${id}/items/selection?${new URLSearchParams({ q })}`),
  bulkItems: (id: string, action: 'enable' | 'archive' | 'delete', ids: string[]) => call<BulkResult>(`datasets/${id}/items/bulk`, 'POST', { action, ids }),
  create: (value: { name: string; description: string; evaluation_purpose?: 'rag' | 'retrieval' | 'both' }) => call<Dataset>('datasets', 'POST', value),
  import: (form: FormData) => call<Dataset>('datasets/import', 'POST', form),
  generate: (form: FormData) => call<Dataset>('datasets/generate', 'POST', form),
  update: (id: string, value: { name: string; description: string; evaluation_purpose?: 'rag' | 'retrieval' | 'both' }) => call<Dataset>(`datasets/${id}`, 'PUT', value),
  delete: (id: string) => call<void>(`datasets/${id}`, 'DELETE'),
  cancel: (id: string) => call<Dataset>(`datasets/${id}/cancel`, 'POST'),
  retry: (id: string) => call<Dataset>(`datasets/${id}/retry`, 'POST'),
  sources: (id: string, signal?: AbortSignal) => call<DatasetSources>(`datasets/${id}/sources`, 'GET', undefined, signal),
  documents: (id: string, form: FormData) => call<Dataset>(`datasets/${id}/documents`, 'POST', form),
  syncDify: (id: string, form: FormData) => call<Dataset>(`datasets/${id}/sync-dify`, 'POST', form),
  configure: (id: string, value: DatasetGenerationOptions & { language: Language }) => call<Dataset>(`datasets/${id}/generation-config`, 'PUT', value),
  build: (id: string, mode: 'append' | 'replace_generated') => call<Dataset>(`datasets/${id}/build?mode=${mode}`, 'POST'),
  append: (id: string, form: FormData) => call<Dataset>(`datasets/${id}/import`, 'POST', form),
  saveItem: (id: string, itemId: string | undefined, value: unknown) => call<Dataset>(`datasets/${id}/items${itemId ? `/${itemId}` : ''}`, itemId ? 'PUT' : 'POST', value),
  deleteItem: (id: string, itemId: string) => call<void>(`datasets/${id}/items/${itemId}`, 'DELETE'),
  settings: () => call<DatasetSettings>('dataset-settings'),
  saveSettings: (value: { max_concurrent?: number; default_llm?: LlmSelection | null; default_embedding_model?: LlmSelection | null; default_critic_llm?: LlmSelection | null; prompt?: string | null; critic_prompt?: string | null; default_judge_llm?: LlmSelection | null; default_reranker?: LlmSelection | null; scoring_engine?: 'deepeval' | 'ragas'; judge_prompt?: string | null }) => call<DatasetSettings>('dataset-settings', 'PUT', value),
  export: async (id: string, format: 'json' | 'jsonl' | 'csv' = 'json', ids?: string[]) => {
    const response = await authenticatedFetch(`/api/datasets/${id}/export?format=${format}`, ids ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
    } : undefined);
    if (!response.ok) {
      const value = await response.json().catch(() => null);
      throw new Error(typeof value?.detail === 'string' ? value.detail : `Export failed (${response.status})`);
    }
    return response.blob();
  },
};
export function llmChoices(catalog: Catalog | null, language: Language) {
  return modelChoices(catalog, language, 'llm');
}
export function embeddingChoices(catalog: Catalog | null, language: Language) {
  return modelChoices(catalog, language, 'embedding');
}
export function rerankerChoices(catalog: Catalog | null, language: Language) {
  return modelChoices(catalog, language, 'reranker');
}
function modelChoices(catalog: Catalog | null, language: Language, benchmark: string) {
  return (catalog?.benchmarks.find(item => item.id === benchmark)?.providers || []).flatMap(provider =>
    provider.models.filter(model => model.name).flatMap(model => (model.credentials?.length ? model.credentials : [null]).map(credential => ({
      value: JSON.stringify({ id: provider.id, model: model.alias || model.name, credential_id: credential?.id || null }),
      // Legacy endpoint-only models get a synthetic credential named `Default`.
      // Keep its id in the selection payload, but don't expose that implementation
      // detail in user-facing model labels.
      label: `${providerDisplayName(provider.provider || provider.id, language, provider.label)} · ${model.alias || model.name}${credential && credential.id !== 'legacy' && credential.name?.toLocaleLowerCase() !== 'default' ? ` · ${credential.name}` : ''}`,
    }))),
  );
}
export function selectionValue(selection: LlmSelection | null) {
  return selection ? JSON.stringify({ id: selection.id, model: selection.model, credential_id: selection.credential_id || null }) : '';
}
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
