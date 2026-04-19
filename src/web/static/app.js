/**
 * app.js — boot + tab navigation + periodic refresh.
 *
 * Loaded last (after all per-tab modules). Wires up .nav-tab clicks, calls
 * every loader in parallel, then kicks off the 30s polling refresh and
 * the SSE connection for the Live Activity tab.
 */

document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

async function loadAll() {
  const results = await Promise.allSettled([
    loadOverview(),
    loadRecentFiles(),
    loadDirectories(),
    loadKnowledge(),
    loadScheduling(),
    loadLLMConfig(),
    loadRouting(),
    loadEmbeddingConfig(),
    loadTriage(),
    loadSummaryProgress(),
  ]);
  const failed = results.filter(r => r.status === 'rejected');
  if (failed.length > 0) {
    console.warn('Dashboard: some loaders failed:', failed.map(r => r.reason?.message || r.reason));
  }
}

loadAll();
setInterval(loadAll, 30000);
setInterval(renderActiveTasks, 1000);
connectSSE();
