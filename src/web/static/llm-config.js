/**
 * llm-config.js — LLM Config tab.
 *
 * Three independent sub-sections, each with its own dirty flag and save button:
 *   1. Provider editor        → /api/llm-config        (~/.agent_sys/llm_config.yaml)
 *   2. Per-function routing   → /api/llm-routing       (config/default.yaml: llm)
 *   3. Embedding provider     → /api/embedding-config  (config/default.yaml: memory.embedding)
 *
 * Note: routing-ui.js is loaded AFTER this file and monkey-patches
 * renderRoutingFunctions() with a richer table (vision toggles, per-function
 * model overrides). _providerOptions / _tierOptions / routingData / markRoutingDirty
 * must stay as globals here for that override to work.
 */

// ── Provider editor ─────────────────────────────────────────────

let llmConfig = null;
let llmDirty = false;

const PROVIDER_TYPES = {
  openai:                { label: 'OpenAI',                keyEnv: 'OPENAI_API_KEY',                defaultUrl: '',                                          placeholder: 'gpt-4o-mini' },
  openai_compatible:     { label: 'OpenAI Compatible',     keyEnv: 'OPENAI_COMPATIBLE_API_KEY',     defaultUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', placeholder: 'qwen3.6-plus' },
  anthropic:             { label: 'Anthropic',             keyEnv: 'ANTHROPIC_API_KEY',             defaultUrl: '',                                          placeholder: 'claude-sonnet-4-20250514' },
  anthropic_compatible:  { label: 'Anthropic Compatible',  keyEnv: 'ANTHROPIC_COMPATIBLE_API_KEY',  defaultUrl: 'https://api.minimaxi.com/anthropic',        placeholder: 'MiniMax-M2.7' },
};

function markLLMDirty() {
  llmDirty = true;
  document.getElementById('saveLLMBtn').style.display = '';
  document.getElementById('llmSaveHint').style.display = '';
}

async function loadLLMConfig() {
  const data = await fetch(API + '/api/llm-config').then(r => r.json());
  llmConfig = data.config || {};

  const select = document.getElementById('defaultProvider');
  const providers = Object.keys(llmConfig.providers || {});
  select.innerHTML = providers.map(p =>
    `<option value="${p}" ${p === llmConfig.default_provider ? 'selected' : ''}>${PROVIDER_TYPES[p]?.label || p}</option>`
  ).join('');

  renderProviders();
}

function renderProviders() {
  const container = document.getElementById('providerList');
  const providers = llmConfig.providers || {};

  if (Object.keys(providers).length === 0) {
    container.innerHTML = '<div class="empty-state">No providers configured. Click "+ Add Provider" to get started.</div>';
    return;
  }

  container.innerHTML = Object.entries(providers).map(([name, cfg]) => {
    const isActive = name === llmConfig.default_provider;
    const typeInfo = PROVIDER_TYPES[name] || { label: name, keyEnv: '', placeholder: '' };
    const models = cfg.models || {};
    const needsUrl = name === 'openai_compatible' || name === 'anthropic_compatible';

    return `
      <div class="provider-card ${isActive ? 'active-provider' : ''}" id="provider-${name}">
        <div class="provider-header">
          <div class="provider-name">
            ${isActive ? '<span class="active-dot"></span>' : ''}
            ${typeInfo.label}
            ${isActive ? '<span style="font-size:11px;color:var(--accent-green);font-weight:normal;">(active)</span>' : ''}
          </div>
          <div class="provider-actions">
            ${!isActive ? `<button class="btn btn-primary" onclick="setDefaultProvider('${name}')">Set as Default</button>` : ''}
            <button class="btn btn-danger" onclick="deleteProvider('${name}')">Remove</button>
          </div>
        </div>

        ${needsUrl ? `
          <div class="form-group">
            <label class="form-label">Base URL</label>
            <input class="form-input mono" data-provider="${name}" data-field="base_url"
                   value="${cfg.base_url || ''}" placeholder="https://..."
                   oninput="markLLMDirty()">
          </div>
        ` : ''}

        <div class="form-group">
          <label class="form-label">API Key (${cfg.api_key_env || typeInfo.keyEnv})</label>
          <input class="form-input mono" type="password" data-provider="${name}" data-field="api_key"
                 placeholder="${cfg.has_key ? '••••••••  (saved)' : 'Enter API key...'}"
                 oninput="markLLMDirty()">
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Fast Model</label>
            <input class="form-input mono" data-provider="${name}" data-field="model_fast"
                   value="${models.fast || ''}" placeholder="${typeInfo.placeholder}"
                   oninput="markLLMDirty()">
          </div>
          <div class="form-group">
            <label class="form-label">Strong Model</label>
            <input class="form-input mono" data-provider="${name}" data-field="model_strong"
                   value="${models.strong || ''}" placeholder="${typeInfo.placeholder}"
                   oninput="markLLMDirty()">
          </div>
        </div>

        ${models.vision ? `
          <div class="form-group" style="max-width:calc(50% - 6px);">
            <label class="form-label">Vision Model</label>
            <input class="form-input mono" data-provider="${name}" data-field="model_vision"
                   value="${models.vision || ''}" oninput="markLLMDirty()">
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

function setDefaultProvider(name) {
  llmConfig.default_provider = name;
  document.getElementById('defaultProvider').value = name;
  markLLMDirty();
  renderProviders();
}

function deleteProvider(name) {
  if (name === llmConfig.default_provider) {
    showToast('Cannot delete the active provider. Switch to another first.', 'error');
    return;
  }
  if (!confirm(`Remove provider "${name}"?`)) return;
  delete llmConfig.providers[name];
  markLLMDirty();
  renderProviders();

  const select = document.getElementById('defaultProvider');
  select.innerHTML = Object.keys(llmConfig.providers).map(p =>
    `<option value="${p}" ${p === llmConfig.default_provider ? 'selected' : ''}>${PROVIDER_TYPES[p]?.label || p}</option>`
  ).join('');
}

function addNewProvider() {
  const existing = Object.keys(llmConfig.providers || {});
  const available = Object.entries(PROVIDER_TYPES).filter(([k]) => !existing.includes(k));

  if (available.length === 0) {
    showToast('All provider types already configured.', 'error');
    return;
  }

  const choice = prompt(
    'Choose provider type:\n' +
    available.map(([k, v], i) => `  ${i + 1}. ${v.label} (${k})`).join('\n') +
    '\n\nEnter number:'
  );

  const idx = parseInt(choice) - 1;
  if (isNaN(idx) || idx < 0 || idx >= available.length) return;

  const [name, info] = available[idx];
  if (!llmConfig.providers) llmConfig.providers = {};
  llmConfig.providers[name] = {
    base_url: info.defaultUrl,
    api_key_env: info.keyEnv,
    models: { fast: info.placeholder, strong: info.placeholder },
    has_key: false,
  };
  markLLMDirty();
  renderProviders();

  const select = document.getElementById('defaultProvider');
  select.innerHTML += `<option value="${name}">${info.label}</option>`;
}

async function saveLLMConfig() {
  const payload = {
    default_provider: document.getElementById('defaultProvider').value,
    providers: {},
    env_vars: {},
  };

  for (const [name, cfg] of Object.entries(llmConfig.providers || {})) {
    const card = document.getElementById('provider-' + name);
    if (!card) continue;

    const getVal = (field) => {
      const el = card.querySelector(`[data-field="${field}"]`);
      return el ? el.value : '';
    };

    const baseUrl = getVal('base_url');
    const apiKey = getVal('api_key');
    const fast = getVal('model_fast');
    const strong = getVal('model_strong');
    const vision = getVal('model_vision');

    const prov = { api_key_env: cfg.api_key_env };
    if (baseUrl) prov.base_url = baseUrl;
    const models = {};
    if (fast) models.fast = fast;
    if (strong) models.strong = strong;
    if (vision) models.vision = vision;
    if (Object.keys(models).length > 0) prov.models = models;
    payload.providers[name] = prov;

    if (apiKey) {
      payload.env_vars[cfg.api_key_env || PROVIDER_TYPES[name]?.keyEnv || ''] = apiKey;
    }
  }

  try {
    const resp = await fetch(API + '/api/llm-config', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify(payload),
    }).then(r => r.json());

    if (resp.success) {
      showToast('Configuration saved. Applying to daemon...', 'success');
      await reloadDaemonConfig();
      llmDirty = false;
      document.getElementById('saveLLMBtn').style.display = 'none';
      await loadLLMConfig();
    } else {
      showToast('Save failed: ' + (resp.error || 'Unknown error'), 'error');
    }
  } catch (e) {
    showToast('Save failed: ' + e.message, 'error');
  }
}

// ── Per-function routing ────────────────────────────────────────

let routingData = null;

function markRoutingDirty() {
  document.getElementById('saveRoutingBtn').style.display = '';
}

async function loadRouting() {
  try {
    routingData = await fetch(API + '/api/llm-routing').then(r => r.json());
    if (routingData.error) throw new Error(routingData.error);
    renderRoutingDefaults();
    renderRoutingFunctions();
  } catch (e) {
    document.getElementById('routingFunctions').innerHTML =
      `<div style="color:var(--accent-red);">Failed to load: ${e.message}</div>`;
  }
}

function _providerOptions(selected, allowEmpty) {
  const provs = Object.keys(routingData.providers || {});
  let html = allowEmpty ? `<option value="" ${!selected ? 'selected' : ''}>(default)</option>` : '';
  provs.forEach(p => {
    html += `<option value="${p}" ${p === selected ? 'selected' : ''}>${p}</option>`;
  });
  return html;
}

function _tierOptions(providerName, selected, allowEmpty) {
  const tiers = (routingData.providers || {})[providerName]?.tiers || ['fast', 'strong'];
  const allTiers = [...new Set(['fast', 'strong', 'vision', 'reasoning', ...tiers])];
  let html = allowEmpty ? `<option value="" ${!selected ? 'selected' : ''}>(default)</option>` : '';
  allTiers.forEach(t => {
    html += `<option value="${t}" ${t === selected ? 'selected' : ''}>${t}</option>`;
  });
  return html;
}

function renderRoutingDefaults() {
  const d = routingData.defaults || {};
  document.getElementById('routingDefaults').innerHTML = `
    <div class="form-group">
      <label class="form-label" style="font-size:12px;">Text Provider</label>
      <select class="form-select" data-rdef="text_provider" onchange="markRoutingDirty()">
        ${_providerOptions(d.text_provider, true)}
      </select>
    </div>
    <div class="form-group">
      <label class="form-label" style="font-size:12px;">Text Tier</label>
      <select class="form-select" data-rdef="text_tier" onchange="markRoutingDirty()">
        ${_tierOptions(d.text_provider, d.text_tier || 'fast', false)}
      </select>
    </div>
    <div class="form-group">
      <label class="form-label" style="font-size:12px;">Vision Provider</label>
      <select class="form-select" data-rdef="vision_provider" onchange="markRoutingDirty()">
        ${_providerOptions(d.vision_provider, true)}
      </select>
    </div>
    <div class="form-group">
      <label class="form-label" style="font-size:12px;">Vision Tier</label>
      <select class="form-select" data-rdef="vision_tier" onchange="markRoutingDirty()">
        ${_tierOptions(d.vision_provider, d.vision_tier || 'vision', false)}
      </select>
    </div>
  `;
}

function renderRoutingFunctions() {
  const funcs = routingData.functions || {};
  const allFuncs = [...new Set(['triage', 'summarize', 'analyze', 'report', 'brief', 'profile', 'search', ...Object.keys(funcs)])];
  const labels = {
    triage: 'Triage', summarize: 'Summarize', analyze: 'Analyze',
    report: 'Report', brief: 'Brief', profile: 'Profile', search: 'Search',
  };

  document.getElementById('routingFunctions').innerHTML = `
    <table class="data-table">
      <thead><tr>
        <th>Function</th>
        <th>Text Provider</th>
        <th>Text Tier</th>
        <th>Vision Provider</th>
        <th>Vision Tier</th>
      </tr></thead>
      <tbody>
        ${allFuncs.map(fn => {
          const fc = funcs[fn] || {};
          return `<tr>
            <td style="font-weight:600;color:var(--text-primary);">${labels[fn] || fn}</td>
            <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="text_provider" onchange="markRoutingDirty()">
              ${_providerOptions(fc.text_provider, true)}
            </select></td>
            <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="text_tier" onchange="markRoutingDirty()">
              ${_tierOptions(fc.text_provider, fc.text_tier, true)}
            </select></td>
            <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="vision_provider" onchange="markRoutingDirty()">
              ${_providerOptions(fc.vision_provider, true)}
            </select></td>
            <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="vision_tier" onchange="markRoutingDirty()">
              ${_tierOptions(fc.vision_provider, fc.vision_tier, true)}
            </select></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
  `;
}

async function saveRouting() {
  const defaults = {};
  document.querySelectorAll('[data-rdef]').forEach(el => {
    defaults[el.dataset.rdef] = el.value;
  });

  const functions = {};
  const allFns = new Set();
  document.querySelectorAll('[data-rfn]').forEach(el => allFns.add(el.dataset.rfn));
  allFns.forEach(fn => {
    const fc = {};
    document.querySelectorAll(`[data-rfn="${fn}"]`).forEach(el => {
      if (el.value) fc[el.dataset.rfield] = el.value;
    });
    functions[fn] = fc;
  });

  try {
    const resp = await fetch(API + '/api/llm-routing', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify({ defaults, functions }),
    }).then(r => r.json());
    if (resp.success) {
      document.getElementById('saveRoutingBtn').style.display = 'none';
      await loadRouting();
      await reloadDaemonConfig();
    } else {
      showToast('Error: ' + (resp.error || 'unknown'), 'error');
    }
  } catch (e) {
    showToast('Save failed: ' + e.message, 'error');
  }
}

// ── Embedding provider ──────────────────────────────────────────

let embeddingConfig = null;

function markEmbeddingDirty() {
  document.getElementById('saveEmbeddingBtn').style.display = '';
}

async function loadEmbeddingConfig() {
  try {
    embeddingConfig = await fetch(API + '/api/embedding-config').then(r => r.json());
    if (embeddingConfig.error) throw new Error(embeddingConfig.error);
    renderEmbeddingConfig();
  } catch (e) {
    document.getElementById('embeddingConfigUI').innerHTML =
      `<div style="color:var(--accent-red);">Failed to load: ${e.message}</div>`;
  }
}

function renderEmbeddingConfig() {
  const cfg = embeddingConfig.config || {};
  const live = embeddingConfig.live || {};
  const provider = cfg.provider || 'local';

  const presets = {
    local:     { label: 'Local (sentence-transformers)', desc: 'Runs on CPU, no API calls. Good for privacy.' },
    dashscope: { label: 'DashScope (Qwen)',              desc: 'Qwen3-VL-Embedding via DashScope API.' },
    openai:    { label: 'OpenAI',                        desc: 'text-embedding-3-small/large via OpenAI API.' },
  };

  document.getElementById('embeddingConfigUI').innerHTML = `
    <div style="display:flex;gap:12px;margin-bottom:20px;">
      ${Object.entries(presets).map(([k, v]) => {
        const active = k === provider;
        return `
          <div onclick="selectEmbeddingProvider('${k}')" style="
            flex:1;padding:14px;border-radius:8px;cursor:pointer;
            border:2px solid ${active ? 'var(--accent)' : 'var(--border)'};
            background:${active ? 'rgba(88,166,255,0.08)' : 'var(--bg-secondary)'};
          ">
            <div style="font-weight:600;font-size:14px;color:${active ? 'var(--accent)' : 'var(--text-primary)'};">
              ${active ? '&#x2713; ' : ''}${v.label}
            </div>
            <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">${v.desc}</div>
          </div>`;
      }).join('')}
    </div>

    <div id="embeddingFields" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:600px;">
      ${provider === 'local' ? `
        <div class="form-group">
          <label class="form-label" style="font-size:12px;">Local Model</label>
          <input class="form-input mono" id="emb_local_model" value="${cfg.local_model || 'all-MiniLM-L6-v2'}" oninput="markEmbeddingDirty()">
        </div>
      ` : `
        <div class="form-group">
          <label class="form-label" style="font-size:12px;">API Key Env Var</label>
          <input class="form-input mono" id="emb_api_key_env" value="${cfg.api_key_env || (provider === 'dashscope' ? 'DASHSCOPE_API_KEY' : 'OPENAI_API_KEY')}" oninput="markEmbeddingDirty()">
        </div>
        <div class="form-group">
          <label class="form-label" style="font-size:12px;">API Base URL</label>
          <input class="form-input mono" id="emb_api_base_url" value="${cfg.api_base_url || (provider === 'dashscope' ? 'https://dashscope.aliyuncs.com/compatible-mode/v1' : 'https://api.openai.com/v1')}" oninput="markEmbeddingDirty()">
        </div>
        <div class="form-group">
          <label class="form-label" style="font-size:12px;">Model</label>
          <input class="form-input mono" id="emb_model" value="${cfg.model || (provider === 'dashscope' ? 'text-embedding-v4' : 'text-embedding-3-small')}" oninput="markEmbeddingDirty()">
        </div>
        <div class="form-group">
          <label class="form-label" style="font-size:12px;">Dimensions (0 = auto)</label>
          <input class="form-input mono" id="emb_dimensions" type="number" value="${cfg.dimensions || 0}" oninput="markEmbeddingDirty()">
        </div>
      `}
    </div>

    <div style="margin-top:12px;font-size:12px;color:var(--text-muted);">
      Live status: <strong style="color:${live.available ? 'var(--accent-green)' : 'var(--accent-red)'};">
        ${live.available ? 'available' : 'unavailable'}
      </strong>
      ${live.model ? ' (' + live.model + ')' : ''}
    </div>
  `;
}

function selectEmbeddingProvider(name) {
  if (!embeddingConfig) return;
  const defaults = {
    local:     { provider: 'local',     local_model: 'all-MiniLM-L6-v2' },
    dashscope: { provider: 'dashscope', api_key_env: 'DASHSCOPE_API_KEY', api_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'text-embedding-v4',     dimensions: 1024 },
    openai:    { provider: 'openai',    api_key_env: 'OPENAI_API_KEY',    api_base_url: 'https://api.openai.com/v1',                          model: 'text-embedding-3-small', dimensions: 0 },
  };
  embeddingConfig.config = defaults[name] || defaults.local;
  markEmbeddingDirty();
  renderEmbeddingConfig();
}

async function saveEmbeddingConfig() {
  const cfg = embeddingConfig.config || {};
  const provider = cfg.provider || 'local';
  const payload = { provider };

  if (provider === 'local') {
    const el = document.getElementById('emb_local_model');
    payload.local_model = el ? el.value : 'all-MiniLM-L6-v2';
  } else {
    ['api_key_env', 'api_base_url', 'model'].forEach(f => {
      const el = document.getElementById('emb_' + f);
      if (el && el.value) payload[f] = el.value;
    });
    const dimEl = document.getElementById('emb_dimensions');
    payload.dimensions = dimEl ? parseInt(dimEl.value) || 0 : 0;
  }

  try {
    const resp = await fetch(API + '/api/embedding-config', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify({ embedding: payload }),
    }).then(r => r.json());
    if (resp.success) {
      document.getElementById('saveEmbeddingBtn').style.display = 'none';
      await loadEmbeddingConfig();
      await reloadDaemonConfig();
    } else {
      showToast('Error: ' + (resp.error || 'unknown'), 'error');
    }
  } catch (e) {
    showToast('Save failed: ' + e.message, 'error');
  }
}
