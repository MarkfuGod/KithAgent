/**
 * scheduling.js — Scheduling tab: strategy cards + recent decision timeline.
 */

async function loadSchedulingStrategy() {
  try {
    const [stratData, embData] = await Promise.all([
      fetch(API + '/api/scheduling-strategy').then(r => r.json()),
      fetch(API + '/api/embedding-info').then(r => r.json()),
    ]);

    const container = document.getElementById('strategyCards');
    const policyLabels = {
      triage: 'Triage', summarize: 'Summarize', report: 'Report',
      behavior: 'Behavior', profile: 'Profile', priority: 'Priority',
    };
    const policyColors = {
      always: 'var(--accent-green)', active_only: 'var(--accent)',
      deep_hour_only: 'var(--accent-orange)', daily_only: 'var(--accent)',
      daily: 'var(--accent)', weekly: 'var(--text-muted)',
      frequent: 'var(--accent-green)', after_behavior: 'var(--accent)',
      never: 'var(--accent-red)',
    };

    container.innerHTML = stratData.strategies.map(s => {
      const isActive = s.name === stratData.current;
      return `
        <div onclick="setStrategy('${s.name}')" style="
          flex:1;min-width:200px;padding:16px;border-radius:8px;cursor:pointer;
          border:2px solid ${isActive ? 'var(--accent)' : 'var(--border)'};
          background:${isActive ? 'rgba(88,166,255,0.08)' : 'var(--bg-secondary)'};
          transition:all .2s;
        " onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='${isActive ? 'var(--accent)' : 'var(--border)'}'"
        >
          <div style="font-weight:600;font-size:15px;margin-bottom:4px;text-transform:capitalize;
            color:${isActive ? 'var(--accent)' : 'var(--text-primary)'};">
            ${isActive ? '&#x2713; ' : ''}${s.name}
          </div>
          <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">${s.description}</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px;">
            ${Object.entries(s.policies || {}).map(([k, v]) =>
              `<span style="font-size:11px;padding:2px 6px;border-radius:4px;background:var(--bg-primary);
                color:${policyColors[v] || 'var(--text-secondary)'};">${policyLabels[k] || k}: ${v}</span>`
            ).join('')}
          </div>
        </div>
      `;
    }).join('');

    const embInfo = document.getElementById('strategyEmbeddingInfo');
    if (embData.provider && embData.provider !== 'none') {
      embInfo.innerHTML = `Embedding: <strong style="color:var(--accent);">${embData.provider}</strong>${embData.model ? ' (' + embData.model + ')' : ''}`;
    } else {
      embInfo.innerHTML = 'Embedding: <span style="color:var(--accent-orange);">not configured</span>';
    }
  } catch (e) {
    document.getElementById('strategyCards').innerHTML =
      '<div style="color:var(--text-muted);">Could not load strategies</div>';
  }
}

async function setStrategy(name) {
  try {
    const resp = await fetch(API + '/api/scheduling-strategy', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify({ strategy: name }),
    });
    const data = await resp.json();
    if (data.success) {
      await loadSchedulingStrategy();
      await reloadDaemonConfig();
    } else {
      showToast('Error: ' + (data.error || 'unknown'), 'error');
    }
  } catch (e) {
    showToast('Failed to set strategy: ' + e.message, 'error');
  }
}

async function loadScheduling() {
  await loadSchedulingStrategy();
  const data = await fetch(API + '/api/scheduling?limit=20').then(r => r.json());
  const container = document.getElementById('schedulingContent');

  if (data.length === 0) {
    container.innerHTML = '<div class="empty-state">No scheduling decisions recorded yet.<br>The adaptive cron will log decisions as it runs.</div>';
    return;
  }

  container.innerHTML = data.map(s => {
    const d = typeof s.decision === 'object' ? s.decision : {};
    const agents = d.agents || d.run_agents || [];
    const mode = d.mode || d.analysis_mode || 'normal';
    const nextMin = d.next_interval_minutes || d.interval || '?';
    const reason = d.reasoning || d.reason || '';

    return `
      <div class="sched-card">
        <div class="sched-time">${formatFullTime(s.time)}</div>
        <div style="color:var(--text-primary);font-size:14px;">${reason}</div>
        <div style="display:flex;gap:20px;margin-top:8px;">
          <span style="font-size:12px;color:var(--text-muted);">Mode: <strong style="color:var(--text-secondary)">${mode}</strong></span>
          <span style="font-size:12px;color:var(--text-muted);">Next: <strong style="color:var(--text-secondary)">${nextMin} min</strong></span>
        </div>
        <div class="sched-agents">
          ${(Array.isArray(agents) ? agents : []).map(a => {
            const cls = mode === 'deep' ? 'deep' : mode === 'light' ? 'light' : 'normal';
            return `<span class="agent-chip ${cls}">${typeof a === 'object' ? a.name || JSON.stringify(a) : a}</span>`;
          }).join('')}
        </div>
      </div>
    `;
  }).join('');
}
