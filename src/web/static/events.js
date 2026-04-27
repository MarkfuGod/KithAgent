/**
 * events.js — Live Activity tab.
 *
 * Subscribes to /api/events (SSE), maintains running-task + LLM-call state,
 * and exposes `triggerAgent()` for the manual-run buttons. Auto-reconnects
 * on disconnect.
 */

let sseSource = null;
let llmCallCount = 0;
let totalTokens = 0;
const activeTasks = new Map();
const taskHistory = [];
const llmLogs = [];
const llmCallMap = new Map();
const eventLog = [];

function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource(API + '/api/events');

  sseSource.onopen = () => {
    document.getElementById('sseStatus').textContent = 'Connected';
    document.getElementById('sseStatus').style.color = 'var(--accent-green)';
  };

  sseSource.onerror = () => {
    document.getElementById('sseStatus').textContent = 'Disconnected';
    document.getElementById('sseStatus').style.color = 'var(--accent-red)';
    setTimeout(connectSSE, 5000);
  };

  const handlers = {
    'task.started': handleTaskStarted,
    'task.completed': handleTaskCompleted,
    'task.failed': handleTaskCompleted,
    'llm.request': handleLLMRequest,
    'llm.response': handleLLMResponse,
    'llm.error': handleLLMError,
    'triage.batch_progress': handleTriageProgress,
    'triage.batch_failed': handlePipelineFailure,
    'summarize.file_progress': handleSummarizeProgress,
    'summarize.file_failed': handlePipelineFailure,
    'behavior_insight.started': handleBehaviorInsight,
    'behavior_insight.completed': handleBehaviorInsight,
    'behavior_insight.failed': handleBehaviorInsight,
    'rag_indexer.started': handleRagIndexerEvent,
    'rag_indexer.completed': handleRagIndexerEvent,
  };

  for (const [etype, handler] of Object.entries(handlers)) {
    sseSource.addEventListener(etype, (e) => {
      try {
        const parsed = JSON.parse(e.data);
        handler(parsed.data || parsed);
        addEventLogEntry(parsed);
      } catch (err) { console.warn('SSE parse error:', err); }
    });
  }

  sseSource.onmessage = (e) => {
    try {
      const parsed = JSON.parse(e.data);
      const handler = handlers[parsed.type];
      if (handler) handler(parsed.data || {});
      addEventLogEntry(parsed);
    } catch (err) { /* ignore */ }
  };
}

function handleTaskStarted(data) {
  activeTasks.set(data.task_id, { ...data, startedAt: Date.now() });
  taskHistory.push({ ...data, state: 'started', time: Date.now() });
  if (taskHistory.length > 200) taskHistory.splice(0, taskHistory.length - 200);
  renderActiveTasks();
  renderTaskHistory();
}

function handleTaskCompleted(data) {
  activeTasks.delete(data.task_id);
  taskHistory.push({ ...data, time: Date.now() });
  if (taskHistory.length > 200) taskHistory.splice(0, taskHistory.length - 200);
  renderActiveTasks();
  renderTaskHistory();
}

function renderActiveTasks() {
  const container = document.getElementById('liveActiveTasks');
  document.getElementById('liveRunningCount').textContent = activeTasks.size;
  if (activeTasks.size === 0) {
    container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px;">No active tasks</div>';
    return;
  }
  let html = '';
  for (const [id, t] of activeTasks) {
    const elapsed = ((Date.now() - t.startedAt) / 1000).toFixed(0);
    html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);">
      <div>
        <span class="type-badge" style="background:rgba(63,185,80,0.15);color:var(--accent-green);">${t.name || '?'}</span>
        <span style="color:var(--text-muted);font-size:12px;margin-left:8px;">${id}</span>
      </div>
      <span style="color:var(--accent-orange);font-size:13px;">${elapsed}s</span>
    </div>`;
  }
  container.innerHTML = html;
}

function renderTaskHistory() {
  const container = document.getElementById('liveTaskTimeline');
  if (!container) return;
  const recent = taskHistory.slice(-60).reverse();
  if (recent.length === 0) {
    container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:12px;">Waiting for task events...</div>';
    return;
  }
  container.innerHTML = recent.map(t => {
    const timeStr = new Date(t.time).toLocaleTimeString();
    const state = t.state || 'completed';
    const color = state === 'failed' || t.error ? 'var(--accent-red)' : state === 'started' ? 'var(--accent-orange)' : 'var(--accent-green)';
    return `<div style="display:grid;grid-template-columns:90px 140px 1fr;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;">
      <span style="color:var(--text-muted);">${timeStr}</span>
      <span><span class="type-badge" style="color:${color};">${escapeHtml(t.name || '?')}</span></span>
      <span style="color:var(--text-secondary);">${escapeHtml(state)} ${t.elapsed_s ? '(' + t.elapsed_s + 's)' : ''} ${t.error ? '- ' + escapeHtml(t.error) : ''}</span>
    </div>`;
  }).join('');
}

function handleLLMRequest(data) {
  llmCallCount++;
  document.getElementById('liveLLMCount').textContent = llmCallCount;
  llmLogs.push({ type: 'request', ...data, time: Date.now() });
  const key = data.call_id || `legacy-${Date.now()}-${llmLogs.length}`;
  llmCallMap.set(key, { call_id: key, request: data, time: Date.now() });
  renderLLMLogs();
}

function handleLLMResponse(data) {
  const usage = data.usage || {};
  totalTokens += (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
  document.getElementById('liveTotalTokens').textContent = totalTokens.toLocaleString();
  llmLogs.push({ type: 'response', ...data, time: Date.now() });
  const key = data.call_id || `legacy-response-${Date.now()}-${llmLogs.length}`;
  const existing = llmCallMap.get(key) || { call_id: key, time: Date.now() };
  existing.response = data;
  existing.time = Date.now();
  llmCallMap.set(key, existing);
  if (llmLogs.length > 200) llmLogs.splice(0, llmLogs.length - 200);
  _trimLLMCallMap();
  renderLLMLogs();
}

function handleLLMError(data) {
  llmLogs.push({ type: 'error', ...data, time: Date.now() });
  const key = data.call_id || `legacy-error-${Date.now()}-${llmLogs.length}`;
  const existing = llmCallMap.get(key) || { call_id: key, time: Date.now() };
  existing.error = data;
  existing.time = Date.now();
  llmCallMap.set(key, existing);
  _trimLLMCallMap();
  renderLLMLogs();
}

function _trimLLMCallMap() {
  const entries = Array.from(llmCallMap.entries()).sort((a, b) => (a[1].time || 0) - (b[1].time || 0));
  while (entries.length > 120) {
    const [key] = entries.shift();
    llmCallMap.delete(key);
  }
}

function renderLLMLogs() {
  const container = document.getElementById('liveLLMLogs');
  const recent = Array.from(llmCallMap.values()).sort((a, b) => (b.time || 0) - (a.time || 0)).slice(0, 30);
  container.innerHTML = recent.map(call => {
    const req = call.request || {};
    const res = call.response || {};
    const err = call.error || null;
    const timeStr = new Date(call.time || Date.now()).toLocaleTimeString();
    const usage = res.usage || {};
    const tokens = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
    const taskType = req.task_type || res.task_type || err?.task_type || '?';
    const model = req.model || res.model || err?.model || '?';
    const provider = req.provider || res.provider || err?.provider || '?';
    const isClassifyJson = taskType === 'classify' && (res.content || res.content_preview || '').trim().startsWith('{');
    const content = err ? err.error : (res.content || res.content_preview || 'Waiting for response...');
    const stateColor = err ? 'var(--accent-red)' : res.content || res.content_preview ? 'var(--accent-green)' : 'var(--accent-orange)';
    return `<div style="padding:10px 16px;border-bottom:1px solid var(--border);font-size:12px;">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <span style="color:${stateColor};">[${timeStr}]</span>
        <span class="type-badge">${taskType}</span>
        <span style="color:var(--text-muted);">${provider} / ${model}</span>
        ${req.is_vision ? '<span class="type-badge" style="background:rgba(247,120,186,0.15);color:var(--accent-pink);">vision</span>' : ''}
        <span style="color:var(--text-muted);">${tokens} tokens, ${res.elapsed_ms || err?.elapsed_ms || 0}ms</span>
        <span style="color:var(--text-muted);font-family:monospace;">${call.call_id || ''}</span>
      </div>
      <details ${isClassifyJson ? '' : 'open'} style="margin-top:6px;">
        <summary style="cursor:pointer;color:var(--text-secondary);">${isClassifyJson ? 'JSON classify response hidden by default' : 'LLM details'}</summary>
        <div style="margin-top:6px;color:var(--text-muted);white-space:pre-wrap;max-height:220px;overflow:auto;font-size:11px;">${escapeHtml(content)}${res.content_truncated ? '\n...[truncated]' : ''}</div>
        ${req.prompt_preview ? `<div style="margin-top:8px;color:var(--text-muted);font-size:11px;"><strong>Prompt preview</strong><pre style="white-space:pre-wrap;max-height:160px;overflow:auto;">${escapeHtml(req.prompt_preview)}</pre></div>` : ''}
      </details>
    </div>`;
  }).join('');
}

function handleTriageProgress(data) {
  document.getElementById('liveTriageProgress').innerHTML = `
    <div style="margin-bottom:8px;">
      <span style="color:var(--text-primary);font-weight:600;">Directory: </span>
      <span style="color:var(--accent);font-family:monospace;">~/${data.directory || '?'}</span>
    </div>
    <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
      <span style="color:var(--text-secondary);">Classified so far</span>
      <span style="color:var(--accent-green);font-weight:600;">${data.classified || 0}</span>
    </div>
    <div style="display:flex;justify-content:space-between;">
      <span style="color:var(--text-secondary);">Batch size</span>
      <span>${data.batch_files || 0} files</span>
    </div>
    <div style="display:flex;justify-content:space-between;">
      <span style="color:var(--text-secondary);">Elapsed</span>
      <span>${data.elapsed_s || 0}s</span>
    </div>
  `;
}

function handleSummarizeProgress(data) {
  const pct = data.total > 0 ? Math.round(data.summarized / data.total * 100) : 0;
  document.getElementById('liveSummarizeProgress').innerHTML = `
    <div style="margin-bottom:8px;">
      <span style="color:var(--text-primary);font-weight:600;">Current: </span>
      <span style="color:var(--accent);font-family:monospace;font-size:12px;">${data.path || '?'}</span>
    </div>
    <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
      <span style="color:var(--text-secondary);">Progress</span>
      <span style="color:var(--accent-green);font-weight:600;">${data.summarized || 0} / ${data.total || 0} (${pct}%)</span>
    </div>
    <div class="progress-bar"><div class="fill" style="width:${pct}%"></div></div>
    <div style="margin-top:8px;color:var(--text-muted);font-size:12px;">
      <span class="type-badge">${data.type || '?'}</span>
      ${escapeHtml(data.preview || '')}
    </div>
  `;
}

function handlePipelineFailure(data) {
  addEventLogEntry({ type: 'pipeline.failure', data, ts: Date.now() / 1000 });
}

function handleBehaviorInsight(data) {
  const target = document.getElementById('liveTaskHint');
  if (target) {
    const status = data.status || data.error || 'updated';
    target.textContent = `Behavior insight: ${status}`;
  }
}

function handleRagIndexerEvent(data) {
  const target = document.getElementById('ragLogs');
  if (target) {
    const line = `RAG ${data.indexed_files !== undefined ? 'completed' : 'started'} ` +
      JSON.stringify(data).slice(0, 220);
    const el = document.createElement('div');
    el.textContent = line;
    el.style.cssText = 'padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04);color:var(--accent-green);';
    target.prepend(el);
  }
  if (typeof loadRag === 'function') {
    setTimeout(loadRag, 800);
  }
}

function addEventLogEntry(parsed) {
  eventLog.push(parsed);
  if (eventLog.length > 300) eventLog.splice(0, eventLog.length - 300);
  const container = document.getElementById('liveEventLog');
  const ts = parsed.ts ? new Date(parsed.ts * 1000).toLocaleTimeString() : '';
  const typeColor = {
    'task.started': 'var(--accent-green)', 'task.completed': 'var(--accent)',
    'task.failed': 'var(--accent-red)', 'llm.request': 'var(--accent-orange)',
    'llm.response': 'var(--accent-purple)', 'llm.error': 'var(--accent-red)',
    'triage.batch_progress': 'var(--accent-green)', 'triage.batch_failed': 'var(--accent-red)',
    'summarize.file_progress': 'var(--accent-pink)', 'summarize.file_failed': 'var(--accent-red)',
    'behavior_insight.started': 'var(--accent-orange)',
    'behavior_insight.completed': 'var(--accent-green)',
    'behavior_insight.failed': 'var(--accent-red)',
    'rag_indexer.started': 'var(--accent-orange)',
    'rag_indexer.completed': 'var(--accent-green)',
  }[parsed.type] || 'var(--text-muted)';
  const line = document.createElement('div');
  line.style.cssText = 'padding:2px 0;border-bottom:1px solid var(--border);';
  const data = parsed.data || {};
  const compact = JSON.stringify(data, (key, value) => {
    if (key === 'content' || key === 'prompt_preview') return value ? String(value).slice(0, 160) : value;
    return value;
  }).slice(0, 260);
  line.innerHTML = `<span style="color:var(--text-muted);">${ts}</span> <span style="color:${typeColor};font-weight:500;">${parsed.type}</span> <span style="color:var(--text-secondary);">${escapeHtml(compact)}</span>`;
  container.prepend(line);
  while (container.children.length > 200) container.removeChild(container.lastChild);
}

function clearLLMLogs() { llmLogs.length = 0; llmCallMap.clear(); document.getElementById('liveLLMLogs').innerHTML = ''; }
function clearEventLog() { eventLog.length = 0; document.getElementById('liveEventLog').innerHTML = ''; }

async function triggerAgent(agentName, inputData) {
  const el = document.getElementById('triggerResult');
  el.style.display = 'block';
  el.style.color = 'var(--text-muted)';
  el.textContent = `Submitting ${agentName}...`;
  try {
    const resp = await fetch(API + '/api/trigger-agent', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify({ agent: agentName, input_data: inputData }),
    }).then(r => r.json());
    if (resp.success) {
      el.style.color = 'var(--accent-green)';
      el.textContent = `${agentName} submitted (task_id: ${resp.task_id || '?'}). Watch Live Activity for progress.`;
    } else {
      el.style.color = 'var(--accent-red)';
      el.textContent = `Failed: ${resp.error}`;
    }
  } catch (e) {
    el.style.color = 'var(--accent-red)';
    el.textContent = `Error: ${e.message}`;
  }
  setTimeout(() => { el.style.display = 'none'; }, 8000);
}
