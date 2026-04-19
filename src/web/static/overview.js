/**
 * overview.js — Overview tab: stat cards, type + priority charts, recent files.
 */

let typeChartInstance = null;
let priorityChartInstance = null;

async function loadOverview() {
  const data = await fetch(API + '/api/overview').then(r => r.json());

  document.getElementById('statGrid').innerHTML = `
    <div class="stat-card">
      <div class="label">Total Files Indexed</div>
      <div class="value">${data.total_files.toLocaleString()}</div>
      <div class="sub">${formatBytes(data.total_size_bytes)} total</div>
    </div>
    <div class="stat-card">
      <div class="label">Summarized</div>
      <div class="value">${data.summarized_files.toLocaleString()}</div>
      <div class="sub">${data.total_files > 0 ? Math.round(data.summarized_files / data.total_files * 100) : 0}% complete</div>
      <div class="progress-bar"><div class="fill" style="width:${data.total_files > 0 ? data.summarized_files / data.total_files * 100 : 0}%"></div></div>
    </div>
    <div class="stat-card">
      <div class="label">Knowledge Entries</div>
      <div class="value">${data.knowledge_entries.toLocaleString()}</div>
      <div class="sub">Reports, analyses, profiles</div>
    </div>
    <div class="stat-card">
      <div class="label">Modified (24h)</div>
      <div class="value">${data.recent_24h_modified.toLocaleString()}</div>
      <div class="sub">Files changed today</div>
    </div>
    <div class="stat-card">
      <div class="label">File Types</div>
      <div class="value">${data.file_type_distribution.length}</div>
      <div class="sub">Distinct extensions indexed</div>
    </div>
    <div class="stat-card">
      <div class="label">Daemon</div>
      <div class="value" style="font-size:20px;">${data.daemon.running ? 'PID ' + data.daemon.pid : 'Offline'}</div>
      <div class="sub">${data.daemon.running ? 'Running' : 'Not running'}</div>
    </div>
  `;

  _renderTypeChart(data.file_type_distribution);
  _renderPriorityChart(data.priority_distribution);
  _updateDaemonBadge(data.daemon);
}

function _renderTypeChart(distribution) {
  if (typeof Chart === 'undefined') return;
  const topTypes = distribution.slice(0, 15);
  if (typeChartInstance) typeChartInstance.destroy();
  typeChartInstance = new Chart(document.getElementById('typeChart'), {
    type: 'doughnut',
    data: {
      labels: topTypes.map(t => t.type || 'unknown'),
      datasets: [{
        data: topTypes.map(t => t.count),
        backgroundColor: chartColors.slice(0, topTypes.length),
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'right', labels: { boxWidth: 12, padding: 8, font: { size: 11 } } },
      },
    },
  });
}

function _renderPriorityChart(distribution) {
  if (typeof Chart === 'undefined') return;
  const prioLabels = { 0: 'P0 Hot', 1: 'P1 Warm', 2: 'P2 Cold' };
  const prioColors = ['#f85149', '#d29922', '#8b949e'];
  if (priorityChartInstance) priorityChartInstance.destroy();
  priorityChartInstance = new Chart(document.getElementById('priorityChart'), {
    type: 'bar',
    data: {
      labels: distribution.map(p => prioLabels[p.priority] || 'P' + p.priority),
      datasets: [{
        data: distribution.map(p => p.count),
        backgroundColor: distribution.map((p, i) => prioColors[i] || '#8b949e'),
        borderRadius: 6,
        barThickness: 40,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, grid: { color: '#21262d' } } },
    },
  });
}

function _updateDaemonBadge(daemon) {
  const badge = document.getElementById('daemonBadge');
  const badgeText = document.getElementById('daemonText');
  if (daemon.running) {
    badge.classList.remove('offline');
    badgeText.textContent = 'Daemon Online (PID ' + daemon.pid + ')';
  } else {
    badge.classList.add('offline');
    badgeText.textContent = 'Daemon Offline';
  }
}

async function loadRecentFiles() {
  const data = await fetch(API + '/api/recent?hours=24&limit=50').then(r => r.json());
  const tbody = document.getElementById('recentTable');
  if (data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);">No recent modifications</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(f => `
    <tr>
      <td class="path" title="${f.full_path}">${f.path}</td>
      <td><span class="type-badge">${f.type || '?'}</span></td>
      <td>${formatBytes(f.size)}</td>
      <td>${formatTime(f.modified_at)}</td>
      <td>P${f.priority}</td>
    </tr>
  `).join('');
}
