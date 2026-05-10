import type { ModelDraft, ModelMode } from '../types';

type SavedModelScope = {
  mode?: string;
  provider?: string;
  base_url?: string;
  api_key_env?: string;
  has_key?: boolean;
  ui?: {
    name?: string;
    api_mode?: string;
    api_host?: string;
    api_path?: string;
    model?: string;
    model_display_name?: string;
    model_type?: string;
    capabilities?: {
      vision?: boolean;
      reasoning?: boolean;
      tools?: boolean;
    };
    context_window?: string;
    max_output_tokens?: string;
    improve_compatibility?: boolean;
  };
};

export function normalizeApiBaseUrl(rawHost: string, rawPath = '/chat/completions') {
  let host = (rawHost || '').trim().replace(/\/+$/, '');
  const path = (rawPath || '').trim();
  if (!host) return '';

  const stripCompletionEndpoint = (value: string) => {
    let base = value.replace(/\/+$/, '');
    for (const suffix of ['/chat/completions', '/responses', '/completions']) {
      if (base.endsWith(suffix)) {
        base = base.slice(0, -suffix.length).replace(/\/+$/, '');
        break;
      }
    }
    return base;
  };

  host = stripCompletionEndpoint(host);
  const normalizedPath = path.replace(/^\/+/, '').replace(/\/+$/, '');
  if (normalizedPath && path !== '/chat/completions' && !host.endsWith(`/${normalizedPath}`)) {
    host = stripCompletionEndpoint(`${host}/${normalizedPath}`);
  }
  return stripCompletionEndpoint(host);
}

export function buildModelSettingsPayload(mode: ModelMode, draft: ModelDraft) {
  const provider = draft.api_mode || draft.provider || 'openai_compatible';
  const baseUrl = normalizeApiBaseUrl(draft.api_host || draft.base_url, draft.api_path);
  return {
    mode,
    ...draft,
    provider,
    base_url: baseUrl || draft.base_url,
  };
}

export function normalizeModelMode(value: unknown, fallback: ModelMode = 'api'): ModelMode {
  return value === 'api' || value === 'ollama' || value === 'local' ? value : fallback;
}

export function modelDraftFromSavedScope(
  savedScope: SavedModelScope | undefined,
  fallbackDraft: ModelDraft,
): { mode: ModelMode; draft: ModelDraft } | null {
  if (!savedScope) {
    return null;
  }

  const ui = savedScope.ui || {};
  const capabilities = ui.capabilities || {};
  const apiPath = String(ui.api_path || fallbackDraft.api_path || '/chat/completions');
  const apiHost = String(ui.api_host || savedScope.base_url || fallbackDraft.api_host || fallbackDraft.base_url || '');
  const baseUrl = normalizeApiBaseUrl(apiHost || savedScope.base_url || fallbackDraft.base_url, apiPath);
  const provider = String(savedScope.provider || ui.api_mode || fallbackDraft.provider || 'openai_compatible');
  const model = String(ui.model || fallbackDraft.model || '');

  return {
    mode: normalizeModelMode(savedScope.mode, fallbackDraft.model ? 'api' : 'local'),
    draft: {
      ...fallbackDraft,
      name: String(ui.name || fallbackDraft.name || provider),
      api_mode: String(ui.api_mode || provider),
      provider,
      base_url: baseUrl || String(savedScope.base_url || fallbackDraft.base_url || ''),
      api_host: apiHost,
      api_path: apiPath,
      model,
      model_display_name: String(ui.model_display_name || model || fallbackDraft.model_display_name || ''),
      model_type: String(ui.model_type || fallbackDraft.model_type || 'chat'),
      api_key: '',
      api_key_env: String(savedScope.api_key_env || fallbackDraft.api_key_env || ''),
      has_key: Boolean(savedScope.has_key),
      improve_compatibility: ui.improve_compatibility === undefined
        ? fallbackDraft.improve_compatibility
        : Boolean(ui.improve_compatibility),
      vision: Boolean(capabilities.vision),
      reasoning: Boolean(capabilities.reasoning),
      tools: capabilities.tools === undefined ? fallbackDraft.tools : Boolean(capabilities.tools),
      context_window: String(ui.context_window || fallbackDraft.context_window || ''),
      max_output_tokens: String(ui.max_output_tokens || fallbackDraft.max_output_tokens || ''),
    },
  };
}
