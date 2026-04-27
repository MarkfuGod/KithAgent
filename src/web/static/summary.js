/**
 * summary.js — Summary Progress tab: stacked bar chart + per-type breakdown.
 */

let summaryChartInstance = null;

async function loadSummaryProgress() {
  const data = await fetch(API + '/api/summary-progress').then(r => r.json());

  document.getElementById('summaryOverview').innerHTML = `
    <div class="stat-grid">
      <div class="stat-card">
        <div class="label">Eligible Files</div>
        <div class="value">${data.total.toLocaleString()}</div>
        <div class="sub">${(data.indexed_total || data.total).toLocaleString()} indexed total</div>
      </div>
      <div class="stat-card">
        <div class="label">Summarized</div>
        <div class="value" style="color:var(--accent-green)">${data.summarized.toLocaleString()}</div>
        <div class="progress-bar"><div class="fill" style="width:${data.percent}%"></div></div>
        <div class="sub">${data.percent}%</div>
      </div>
      <div class="stat-card">
        <div class="label">Pending</div>
        <div class="value" style="color:var(--accent-orange)">${data.pending.toLocaleString()}</div>
      </div>
      <div class="stat-card">
        <div class="label">Excluded / Waiting</div>
        <div class="value" style="color:var(--text-muted)">${(data.excluded_by_triage || 0).toLocaleString()}</div>
        <div class="sub">skip, low, unknown, or untriaged</div>
      </div>
    </div>
  `;

  const topTypes = data.by_type.slice(0, 20);

  if (typeof Chart !== 'undefined') {
    if (summaryChartInstance) summaryChartInstance.destroy();
    summaryChartInstance = new Chart(document.getElementById('summaryChart'), {
      type: 'bar',
      data: {
        labels: topTypes.map(t => t.type || '?'),
        datasets: [
          { label: 'Summarized', data: topTypes.map(t => t.done), backgroundColor: '#3fb950', borderRadius: 4 },
          { label: 'Pending', data: topTypes.map(t => t.total - t.done), backgroundColor: '#30363d', borderRadius: 4 },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { boxWidth: 12 } } },
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, grid: { color: '#21262d' } } },
      },
    });
  }

  document.getElementById('summaryDetails').innerHTML = topTypes.map(t => `
    <div class="progress-detail">
      <span class="ext">${t.type || 'unknown'}</span>
      <div class="progress-bar" style="margin:0"><div class="fill" style="width:${t.percent}%"></div></div>
      <span class="pct">${t.done}/${t.total}</span>
    </div>
  `).join('');
}
