/**
 * rag.js — RAG control room: background indexing controls + debug retrieval.
 */

let ragStatus = null;

async function loadRag() {
  try {
    ragStatus = await fetch(API + '/api/rag/status').then(r => r.json());
    if (ragStatus.error) throw new Error(ragStatus.error);
    renderRagStats();
    renderRagConfig();
    renderRagLogs(ragStatus.logs || []);
  } catch (e) {
    const stats = document.getElementById('ragStats');
    if (stats) {
      stats.innerHTML = `<div style="color:var(--accent-red);padding:16px;">Failed to load RAG: ${escapeHtml(e.message)}</div>`;
    }
  }
}

function renderRagStats() {
  const cfg = ragStatus?.config || {};
  const stats = ragStatus?.stats || {};
  const enabled = cfg.enabled !== false;
  const embedded = stats.embedded_chunks || 0;
  const total = stats.total_chunks || 0;
  const pct = total ? Math.round((embedded / total) * 100) : 0;
  const cards = [
    ['Status', enabled ? 'Enabled' : 'Paused', enabled ? 'var(--accent-green)' : 'var(--accent-orange)'],
    ['Pending Files', stats.pending_files ?? '-', 'var(--accent)'],
    ['Chunks', total, 'var(--accent-purple)'],
    ['Embedded', `${embedded} (${pct}%)`, 'var(--accent-green)'],
    ['Indexed Files', stats.files_indexed || 0, 'var(--text-primary)'],
    ['Startup Delay', `${Math.round((cfg.initial_delay_seconds || 600) / 60)}m`, 'var(--accent-orange)'],
  ];

  document.getElementById('ragStats').innerHTML = cards.map(([label, value, color]) => `
    <div class="stat-card">
      <div class="label">${label}</div>
      <div class="value" style="font-size:24px;color:${color};">${value}</div>
    </div>
  `).join('');
}

function renderRagConfig() {
  const cfg = ragStatus?.config || {};
  const enabled = cfg.enabled !== false;
  document.getElementById('ragConfigPanel').innerHTML = `
    <div style="display:grid;gap:14px;">
      <label style="display:flex;align-items:center;gap:10px;font-size:14px;">
        <input id="rag_enabled" type="checkbox" ${enabled ? 'checked' : ''}>
        Enable delayed background RAG indexing
      </label>

      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;">
        ${ragNumberField('initial_delay_seconds', 'Startup Delay (seconds)', cfg.initial_delay_seconds ?? 600)}
        ${ragNumberField('time_budget_seconds', 'Per-run Time Budget', cfg.time_budget_seconds ?? 90)}
        ${ragNumberField('batch_size', 'Files per Batch', cfg.batch_size ?? 20)}
        ${ragNumberField('embedding_batch_size', 'Embeddings per Batch', cfg.embedding_batch_size ?? 32)}
        ${ragNumberField('chunk_size_chars', 'Chunk Size (chars)', cfg.chunk_size_chars ?? 1600)}
        ${ragNumberField('chunk_overlap_chars', 'Overlap (chars)', cfg.chunk_overlap_chars ?? 250)}
        ${ragNumberField('max_file_size_mb', 'Max File Size (MB)', cfg.max_file_size_mb ?? 5)}
        ${ragNumberField('assistant_top_k', 'Assistant Sources', cfg.assistant_top_k ?? 6)}
      </div>

      <div style="padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);">
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">Eligible triage statuses</div>
        <label style="margin-right:14px;"><input id="rag_status_high" type="checkbox" ${(cfg.allowed_triage_statuses || ['high','medium']).includes('high') ? 'checked' : ''}> high</label>
        <label><input id="rag_status_medium" type="checkbox" ${(cfg.allowed_triage_statuses || ['high','medium']).includes('medium') ? 'checked' : ''}> medium</label>
      </div>
    </div>
  `;
}

function ragNumberField(id, label, value) {
  return `
    <div class="form-group" style="margin-bottom:0;">
      <label class="form-label" style="font-size:12px;">${label}</label>
      <input class="form-input mono" id="rag_${id}" type="number" value="${value}">
    </div>
  `;
}

async function saveRagConfig() {
  const statuses = [];
  if (document.getElementById('rag_status_high')?.checked) statuses.push('high');
  if (document.getElementById('rag_status_medium')?.checked) statuses.push('medium');
  const number = id => Number(document.getElementById(`rag_${id}`)?.value || 0);
  const payload = {
    enabled: Boolean(document.getElementById('rag_enabled')?.checked),
    initial_delay_seconds: number('initial_delay_seconds'),
    time_budget_seconds: number('time_budget_seconds'),
    batch_size: number('batch_size'),
    embedding_batch_size: number('embedding_batch_size'),
    chunk_size_chars: number('chunk_size_chars'),
    chunk_overlap_chars: number('chunk_overlap_chars'),
    max_file_size_mb: number('max_file_size_mb'),
    assistant_top_k: number('assistant_top_k'),
    allowed_triage_statuses: statuses.length ? statuses : ['high', 'medium'],
  };

  try {
    const resp = await fetch(API + '/api/rag/config', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify(payload),
    }).then(r => r.json());
    if (resp.success) {
      showToast('RAG config saved.', 'success');
      await reloadDaemonConfig();
      await loadRag();
    } else {
      showToast('RAG save failed: ' + (resp.error || 'unknown'), 'error');
    }
  } catch (e) {
    showToast('RAG save failed: ' + e.message, 'error');
  }
}

async function triggerRagIndex() {
  const cfg = ragStatus?.config || {};
  const payload = {
    input_data: {
      batch_size: cfg.batch_size || 20,
      embedding_batch_size: cfg.embedding_batch_size || 32,
      time_budget: cfg.time_budget_seconds || 90,
      timeout: Math.max(180, (cfg.time_budget_seconds || 90) + 60),
    },
  };
  try {
    const resp = await fetch(API + '/api/rag/trigger', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify(payload),
    }).then(r => r.json());
    if (resp.success) {
      showToast(`RAG indexer submitted: ${resp.task_id || 'queued'}`, 'success');
      await loadRag();
    } else {
      showToast('RAG trigger failed: ' + (resp.error || 'unknown'), 'error');
    }
  } catch (e) {
    showToast('RAG trigger failed: ' + e.message, 'error');
  }
}

async function debugRagSearch() {
  const q = document.getElementById('ragDebugQuery')?.value.trim();
  const target = document.getElementById('ragDebugResults');
  if (!q) return;
  target.innerHTML = '<div class="loading">Retrieving chunks...</div>';
  try {
    const data = await fetch(API + '/api/rag/debug-search?q=' + encodeURIComponent(q)).then(r => r.json());
    if (data.error) throw new Error(data.error);
    const results = data.results || [];
    if (!results.length) {
      target.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:24px;">No matching chunks yet. RAG may still be indexing.</div>';
      return;
    }
    target.innerHTML = results.map(r => `
      <div style="padding:12px;border:1px solid var(--border);border-radius:8px;margin-bottom:10px;background:var(--bg-secondary);">
        <div style="display:flex;justify-content:space-between;gap:10px;margin-bottom:8px;">
          <strong style="color:var(--accent);">${escapeHtml(r.source_id || r.chunk_id || 'S?')}</strong>
          <span style="font-size:12px;color:var(--text-muted);">score ${r.hybrid_score ?? r.score ?? '-'} · ${(r.modes || [r.retrieval_mode]).join('+')}</span>
        </div>
        <div class="mono" style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">${escapeHtml(r.path || '')}:${r.start_line || '?'}-${r.end_line || '?'}</div>
        <div style="font-size:13px;line-height:1.6;white-space:pre-wrap;">${escapeHtml((r.content || '').slice(0, 700))}</div>
      </div>
    `).join('');
  } catch (e) {
    target.innerHTML = `<div style="color:var(--accent-red);padding:16px;">Debug search failed: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadRagLogs() {
  try {
    const data = await fetch(API + '/api/rag/logs?limit=120').then(r => r.json());
    renderRagLogs(data.logs || []);
  } catch (e) {
    renderRagLogs([`Failed to load logs: ${e.message}`]);
  }
}

function renderRagLogs(lines) {
  const target = document.getElementById('ragLogs');
  if (!target) return;
  if (!lines.length) {
    target.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:16px;">No RAG log lines yet.</div>';
    return;
  }
  target.innerHTML = lines.map(line => `<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04);">${escapeHtml(line)}</div>`).join('');
}
