/**
 * triage.js — Triage tab.
 *
 * Renders three sections:
 *   1. Distribution stats + doughnut chart (Phase 1+2 output).
 *   2. "Triage by Directory" — existing per-status dir breakdown.
 *   3. NEW: "Skipped by LLM/Rules — Which Directories?"
 *      Reads /api/triage/skipped-directories and shows the top trees the
 *      triage agent decided not to spend tokens on. This is the visibility
 *      that replaces the old filesystem.ignore_subpaths black-box — now
 *      everything gets indexed and the dashboard can explain exactly what
 *      was pruned and how many files were in it.
 */

let triageChartInstance = null;

const TRIAGE_STATUS_COLORS = {
  high: '#3fb950', medium: '#58a6ff', low: '#d29922',
  skip: '#8b949e', untriaged: '#f85149', unknown: '#6e7681',
};

async function loadTriage() {
  await _loadTriageDistribution();
  await _loadTriageByDir();
  await _loadSkippedDirectories();
  await _loadFileClusters();
}

async function _loadTriageDistribution() {
  const data = await fetch(API + '/api/triage').then(r => r.json());
  if (!data.available) {
    document.getElementById('triageStats').innerHTML =
      '<div class="empty-state">Triage not yet initialized. Run: agent-sys triage</div>';
    return;
  }

  const dist = data.distribution || {};
  const total = Object.values(dist).reduce((a, b) => a + b, 0);
  const triaged = total - (dist.untriaged || 0);

  document.getElementById('triageStats').innerHTML = `
    <div class="stat-card">
      <div class="label">Total Files</div>
      <div class="value">${total.toLocaleString()}</div>
    </div>
    <div class="stat-card">
      <div class="label">Triaged</div>
      <div class="value" style="color:var(--accent-green)">${triaged.toLocaleString()}</div>
      <div class="progress-bar"><div class="fill" style="width:${total > 0 ? triaged / total * 100 : 0}%"></div></div>
      <div class="sub">${total > 0 ? Math.round(triaged / total * 100) : 0}%</div>
    </div>
    <div class="stat-card">
      <div class="label">High (Worth Summarizing)</div>
      <div class="value" style="color:var(--accent-green)">${(dist.high || 0).toLocaleString()}</div>
    </div>
    <div class="stat-card">
      <div class="label">Skip (Noise)</div>
      <div class="value" style="color:var(--text-muted)">${(dist.skip || 0).toLocaleString()}</div>
    </div>
    <div class="stat-card">
      <div class="label">Untriaged</div>
      <div class="value" style="color:var(--accent-red)">${(dist.untriaged || 0).toLocaleString()}</div>
    </div>
    <div class="stat-card">
      <div class="label">Tokens Saved</div>
      <div class="value" style="font-size:20px;color:var(--accent-purple)">${(dist.skip || 0).toLocaleString()} files skipped</div>
      <div class="sub">Won't waste LLM calls on noise</div>
    </div>
  `;

  if (typeof Chart === 'undefined') return;

  const labels = Object.keys(dist);
  const values = Object.values(dist);
  const colors = labels.map(l => TRIAGE_STATUS_COLORS[l] || '#6e7681');

  if (triageChartInstance) triageChartInstance.destroy();
  triageChartInstance = new Chart(document.getElementById('triageChart'), {
    type: 'doughnut',
    data: {
      labels: labels.map(l => l.charAt(0).toUpperCase() + l.slice(1)),
      datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'right', labels: { boxWidth: 12, padding: 8, font: { size: 12 } } },
      },
    },
  });
}

async function _loadTriageByDir() {
  try {
    const data = await fetch(API + '/api/triage').then(r => r.json());
    const target = document.getElementById('triageByDir');
    if (!data.available || !data.by_directory || data.by_directory.length === 0) {
      target.innerHTML = '<div style="color:var(--text-muted);text-align:center;">No directory breakdown available</div>';
      return;
    }
    target.innerHTML = `<table class="data-table">
      <thead><tr><th>Directory</th><th>Status</th><th>Count</th></tr></thead>
      <tbody>${data.by_directory.map(d => `<tr>
        <td class="path">${escapeHtml(d.directory || '?')}</td>
        <td><span class="type-badge" style="color:${TRIAGE_STATUS_COLORS[d.status] || 'var(--text-muted)'};">${d.status}</span></td>
        <td>${d.count.toLocaleString()}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  } catch (e) {
    // ignore — section is optional
  }
}

async function _loadSkippedDirectories() {
  const target = document.getElementById('triageSkippedDirs');
  if (!target) return;
  try {
    const data = await fetch(API + '/api/triage/skipped-directories?depth=3&limit=40').then(r => r.json());
    if (!data.available || !data.directories || data.directories.length === 0) {
      target.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px;">Nothing has been skipped yet — run triage to populate.</div>';
      return;
    }

    const maxCount = Math.max(...data.directories.map(d => d.count));
    target.innerHTML = `
      <div style="padding:12px 20px 4px;color:var(--text-muted);font-size:12px;">
        ${data.total_skipped.toLocaleString()} files total across ${data.directories.length} top directories.
        Watcher indexes everything; these were marked <code>skip</code> by either the rule-based pass or the LLM.
      </div>
      ${data.directories.map(d => `
        <div class="dir-bar">
          <div class="name" title="${escapeHtml(d.directory)}">${escapeHtml(d.directory)}</div>
          <div class="bar-container" style="max-width:${d.count / maxCount * 100}%">
            <div class="bar-segment" style="width:100%;background:var(--text-muted);"></div>
          </div>
          <div class="count-label">${d.count.toLocaleString()}<br><span style="font-size:10px;">${formatBytes(d.total_size)}</span></div>
        </div>
      `).join('')}
    `;
  } catch (e) {
    target.innerHTML = `<div style="color:var(--accent-red);padding:20px;">Failed to load: ${e.message}</div>`;
  }
}

async function _loadFileClusters() {
  const target = document.getElementById('fileClusterRecommendations');
  if (!target) return;
  try {
    const data = await fetch(API + '/api/file-clusters?depth=3&limit=60').then(r => r.json());
    const clusters = data.clusters || [];
    if (clusters.length === 0) {
      target.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px;">No clusters yet.</div>';
      return;
    }

    const colors = {
      include: 'var(--accent-green)',
      review: 'var(--accent-orange)',
      exclude: 'var(--text-muted)',
    };
    target.innerHTML = `
      <div style="padding:12px 20px;color:var(--text-muted);font-size:12px;">
        ${data.total_clusters.toLocaleString()} clusters detected. Excluding a cluster marks its files <code>skip</code>;
        including marks unreviewed files <code>high</code> so they can be summarized.
      </div>
      <table class="data-table">
        <thead><tr><th>Cluster</th><th>Recommendation</th><th>Status Mix</th><th>Signals</th><th>Decision</th></tr></thead>
        <tbody>${clusters.map(c => {
          const statuses = Object.entries(c.statuses || {}).map(([k, v]) =>
            `<span class="type-badge" style="margin-right:4px;color:${TRIAGE_STATUS_COLORS[k] || 'var(--text-muted)'};">${k}:${v}</span>`
          ).join('');
          const rec = c.recommendation || 'review';
          const prefixArg = escapeHtml(JSON.stringify(c.prefix || ''));
          return `<tr>
            <td class="path" title="${escapeHtml(c.prefix || c.directory)}">${escapeHtml(c.directory)}<br><span style="color:var(--text-muted);font-size:11px;">${c.total.toLocaleString()} files, ${formatBytes(c.total_size || 0)}</span></td>
            <td><span class="type-badge" style="color:${colors[rec] || 'var(--text-muted)'};">${rec}</span><br><span style="color:var(--text-muted);font-size:11px;">${escapeHtml(c.reason || '')}</span></td>
            <td>${statuses}</td>
            <td style="font-size:12px;color:var(--text-muted);">config ${c.config || 0}, data ${c.data || 0}, generated ${c.generated || 0}</td>
            <td>
              <button class="btn btn-success" style="font-size:11px;padding:4px 8px;" onclick="setClusterDecision(${prefixArg},'high')">Include</button>
              <button class="btn btn-danger" style="font-size:11px;padding:4px 8px;" onclick="setClusterDecision(${prefixArg},'skip')">Exclude</button>
            </td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    `;
  } catch (e) {
    target.innerHTML = `<div style="color:var(--accent-red);padding:20px;">Failed to load clusters: ${e.message}</div>`;
  }
}

async function setClusterDecision(prefix, status) {
  try {
    const resp = await fetch(API + '/api/file-clusters/decision', {
      method: 'POST',
      headers: DASHBOARD_JSON_HEADERS,
      body: JSON.stringify({ prefix, status }),
    }).then(r => r.json());
    if (resp.success) {
      showToast(`Updated ${resp.updated} files to ${status}`, 'success');
      await loadTriage();
      await loadSummaryProgress();
    } else {
      showToast(resp.error || 'Cluster update failed', 'error');
    }
  } catch (e) {
    showToast('Cluster update failed: ' + e.message, 'error');
  }
}
