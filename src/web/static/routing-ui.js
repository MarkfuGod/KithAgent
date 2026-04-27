/**
 * routing-ui.js — Per-function LLM routing with vision model configuration.
 *
 * Replaces the inline renderRoutingFunctions() with a richer table that includes:
 *   - Text provider / tier / model override
 *   - Vision toggle (enable multimodal per function)
 *   - Vision provider / model (e.g. qwen-vl-plus)
 *
 * Depends on globals from dashboard.html: routingData, API, markRoutingDirty,
 * _providerOptions, _tierOptions, showToast, reloadDaemonConfig, loadRouting.
 */

/* ── helpers ─────────────────────────────────────────────── */

function _modelInput(fnName, field, value) {
  return `<input type="text" class="form-select"
    style="font-size:12px;padding:4px 8px;width:100%;"
    data-rfn="${fnName}" data-rfield="${field}"
    value="${value || ''}"
    placeholder="(auto)"
    oninput="markRoutingDirty()" />`;
}

function _visionToggle(fnName, enabled) {
  const id = `vision_toggle_${fnName}`;
  return `
    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="${id}"
        data-rfn="${fnName}" data-rfield="vision_enabled"
        ${enabled ? 'checked' : ''}
        onchange="onVisionToggle('${fnName}', this.checked); markRoutingDirty();"
        style="accent-color:var(--accent);width:16px;height:16px;" />
      <span style="font-size:12px;color:${enabled ? 'var(--accent)' : 'var(--text-muted)'};">
        ${enabled ? 'ON' : 'OFF'}
      </span>
    </label>`;
}

/* ── toggle handler ──────────────────────────────────────── */

function onVisionToggle(fnName, enabled) {
  const label = document.querySelector(`#vision_toggle_${fnName}`).parentElement.querySelector('span');
  label.textContent = enabled ? 'ON' : 'OFF';
  label.style.color = enabled ? 'var(--accent)' : 'var(--text-muted)';

  const row = document.getElementById(`vision_detail_${fnName}`);
  if (row) row.style.display = enabled ? '' : 'none';
}

/* ── main render ─────────────────────────────────────────── */

function renderRoutingFunctionsEnhanced() {
  const funcs = routingData.functions || {};
  const allFuncs = [...new Set([
    'triage', 'summarize', 'analyze', 'report', 'brief', 'profile', 'search',
    ...Object.keys(funcs),
  ])];
  const labels = {
    triage: 'Triage', summarize: 'Summarize', analyze: 'Analyze',
    report: 'Report', brief: 'Brief', profile: 'Profile', search: 'Search',
  };
  const visionHint = {
    triage: 'Triage is text-only (file metadata)',
    summarize: 'Summarize images with a VL model',
    analyze: 'Behavior analysis is text-based',
    report: 'Reports are text-based',
    brief: 'Brief summaries are text-based',
    profile: 'Profile building is text-based',
    search: 'Search is text-based',
  };

  let html = `<table class="data-table" style="width:100%;">
    <thead><tr>
      <th style="min-width:90px;">Function</th>
      <th>Text Provider</th>
      <th>Text Tier</th>
      <th style="min-width:120px;">Text Model</th>
      <th style="min-width:60px;text-align:center;">Vision</th>
    </tr></thead>
    <tbody>`;

  allFuncs.forEach(fn => {
    const fc = funcs[fn] || {};
    const hasVision = !!(fc.vision_enabled || fc.vision_model || fc.vision_provider);

    html += `<tr>
      <td style="font-weight:600;color:var(--text-primary);">${labels[fn] || fn}</td>
      <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="text_provider" onchange="markRoutingDirty()">
        ${_providerOptions(fc.text_provider, true)}
      </select></td>
      <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="text_tier" onchange="markRoutingDirty()">
        ${_tierOptions(fc.text_provider, fc.text_tier, true)}
      </select></td>
      <td>${_modelInput(fn, 'text_model', fc.text_model)}</td>
      <td style="text-align:center;">${_visionToggle(fn, hasVision)}</td>
    </tr>`;

    // Vision detail row (collapsible)
    html += `<tr id="vision_detail_${fn}" style="display:${hasVision ? '' : 'none'};background:rgba(88,166,255,0.04);">
      <td style="padding-left:24px;font-size:12px;color:var(--text-muted);">
        ↳ Vision
        <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${visionHint[fn] || ''}</div>
      </td>
      <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="vision_provider" onchange="markRoutingDirty()">
        ${_providerOptions(fc.vision_provider, true)}
      </select></td>
      <td><select class="form-select" style="font-size:12px;padding:4px 8px;" data-rfn="${fn}" data-rfield="vision_tier" onchange="markRoutingDirty()">
        ${_tierOptions(fc.vision_provider, fc.vision_tier, true)}
      </select></td>
      <td colspan="2">${_modelInput(fn, 'vision_model', fc.vision_model)}</td>
    </tr>`;
  });

  html += '</tbody></table>';
  document.getElementById('routingFunctions').innerHTML = html;
}

/* ── override saveRouting to include vision_enabled + model fields ── */

const _originalSaveRouting = typeof saveRouting === 'function' ? saveRouting : null;

async function saveRoutingEnhanced() {
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
      const field = el.dataset.rfield;
      if (field === 'vision_enabled') {
        fc[field] = el.checked;
      } else {
        const val = el.value.trim();
        if (val) fc[field] = val;
      }
    });

    // If vision is disabled, strip vision fields from saved config
    if (!fc.vision_enabled) {
      delete fc.vision_provider;
      delete fc.vision_tier;
      delete fc.vision_model;
    }
    delete fc.vision_enabled;

    functions[fn] = fc;
  });

  try {
    const resp = await fetch(API + '/api/llm-routing', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify({defaults, functions}),
    }).then(r => r.json());
    if (resp.success) {
      document.getElementById('saveRoutingBtn').style.display = 'none';
      showToast('Routing saved', 'success');
      await loadRouting();
      await reloadDaemonConfig();
    } else {
      showToast('Error: ' + (resp.error || 'unknown'), 'error');
    }
  } catch (e) { showToast('Save failed: ' + e.message, 'error'); }
}

/* ── hook into existing functions ────────────────────────── */

(function init() {
  const origRender = window.renderRoutingFunctions;
  window.renderRoutingFunctions = renderRoutingFunctionsEnhanced;

  window.saveRouting = saveRoutingEnhanced;
})();
