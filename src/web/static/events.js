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
const llmLogs = [];
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
    'triage.batch_progress': handleTriageProgress,
    'summarize.file_progress': handleSummarizeProgress,
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
  renderActiveTasks();
}

function handleTaskCompleted(data) {
  activeTasks.delete(data.task_id);
  renderActiveTasks();
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

function handleLLMRequest(data) {
  llmCallCount++;
  document.getElementById('liveLLMCount').textContent = llmCallCount;
  llmLogs.push({ type: 'request', ...data, time: Date.now() });
  renderLLMLogs();
}

function handleLLMResponse(data) {
  const usage = data.usage || {};
  totalTokens += (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
  document.getElementById('liveTotalTokens').textContent = totalTokens.toLocaleString();
  llmLogs.push({ type: 'response', ...data, time: Date.now() });
  if (llmLogs.length > 200) llmLogs.splice(0, llmLogs.length - 200);
  renderLLMLogs();
}

function renderLLMLogs() {
  const container = document.getElementById('liveLLMLogs');
  const recent = llmLogs.slice(-30).reverse();
  container.innerHTML = recent.map(l => {
    const timeStr = new Date(l.time).toLocaleTimeString();
    if (l.type === 'request') {
      return `<div style="padding:8px 16px;border-bottom:1px solid var(--border);font-size:12px;">
        <span style="color:var(--accent-orange);">[${timeStr}]</span>
        <span class="type-badge">${l.task_type || '?'}</span>
        <span style="color:var(--text-muted);">→ ${l.provider || '?'} / ${l.model || '?'}</span>
        ${l.is_vision ? '<span class="type-badge" style="background:rgba(247,120,186,0.15);color:var(--accent-pink);">vision</span>' : ''}
      </div>`;
    }
    const tokens = (l.usage?.prompt_tokens || 0) + (l.usage?.completion_tokens || 0);
    return `<div style="padding:8px 16px;border-bottom:1px solid var(--border);font-size:12px;">
      <span style="color:var(--accent-green);">[${timeStr}]</span>
      <span style="color:var(--text-secondary);">${l.model || '?'}</span>
      <span style="color:var(--text-muted);">${tokens} tokens, ${l.elapsed_ms || 0}ms</span>
      <div style="color:var(--text-muted);margin-top:4px;white-space:pre-wrap;max-height:60px;overflow:hidden;font-size:11px;">${escapeHtml(l.content_preview || '')}</div>
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

function addEventLogEntry(parsed) {
  eventLog.push(parsed);
  if (eventLog.length > 300) eventLog.splice(0, eventLog.length - 300);
  const container = document.getElementById('liveEventLog');
  const ts = parsed.ts ? new Date(parsed.ts * 1000).toLocaleTimeString() : '';
  const typeColor = {
    'task.started': 'var(--accent-green)', 'task.completed': 'var(--accent)',
    'task.failed': 'var(--accent-red)', 'llm.request': 'var(--accent-orange)',
    'llm.response': 'var(--accent-purple)', 'triage.batch_progress': 'var(--accent-green)',
    'summarize.file_progress': 'var(--accent-pink)',
  }[parsed.type] || 'var(--text-muted)';
  const line = document.createElement('div');
  line.style.cssText = 'padding:2px 0;border-bottom:1px solid var(--border);';
  line.innerHTML = `<span style="color:var(--text-muted);">${ts}</span> <span style="color:${typeColor};font-weight:500;">${parsed.type}</span> <span style="color:var(--text-secondary);">${JSON.stringify(parsed.data || {}).slice(0, 120)}</span>`;
  container.prepend(line);
  while (container.children.length > 200) container.removeChild(container.lastChild);
}

function clearLLMLogs() { llmLogs.length = 0; document.getElementById('liveLLMLogs').innerHTML = ''; }
function clearEventLog() { eventLog.length = 0; document.getElementById('liveEventLog').innerHTML = ''; }

async function triggerAgent(agentName, inputData) {
  const el = document.getElementById('triggerResult');
  el.style.display = 'block';
  el.style.color = 'var(--text-muted)';
  el.textContent = `Submitting ${agentName}...`;
  try {
    const resp = await fetch(API + '/api/trigger-agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
