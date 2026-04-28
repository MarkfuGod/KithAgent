/**
 * insights.js — shared personal insights tab.
 */

async function loadInsights() {
  const hero = document.getElementById('insightsHero');
  if (!hero) return;
  try {
    const data = await fetch(API + '/api/insights?limit=16').then(r => r.json());
    renderInsightsHero(data);
    renderInsightsSuggestions(data.suggestions || []);
    renderInsightsFileOrg(data.file_organization || []);
    renderInsightsCleanup(data.cleanup_candidates || []);
    renderInsightsInterests(data.video_interests || [], data.web_interests || {});
  } catch (e) {
    hero.innerHTML = `<div class="empty-state">Failed to load insights: ${escapeHtml(e.message)}</div>`;
  }
}

function renderInsightsHero(data) {
  const overview = data.overview || {};
  const confidence = Math.round((overview.confidence || 0) * 100);
  document.getElementById('insightsHero').innerHTML = `
    <div class="insights-hero-card">
      <div>
        <div class="insights-kicker">Personal Context</div>
        <h2>Today in Kith</h2>
        <p>
          ${confidence}% confidence from ${Number(overview.total_files || 0).toLocaleString()} indexed files,
          ${Number(overview.source_records || 0).toLocaleString()} source records, and
          ${Number(overview.insight_items || 0).toLocaleString()} insight items.
        </p>
      </div>
      <div class="insights-metrics">
        ${insightMetric('Recent 7d', Number(overview.recent_7d_modified || 0).toLocaleString())}
        ${insightMetric('RAG pending', Number(overview.rag_pending || 0).toLocaleString())}
        ${insightMetric('Total size', formatBytes(overview.total_size_bytes || 0))}
        ${insightMetric('Generated', formatTime(data.generated_at))}
      </div>
    </div>
  `;
}

function insightMetric(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function renderInsightsSuggestions(items) {
  const target = document.getElementById('insightsSuggestions');
  if (!target) return;
  if (!items.length) {
    target.innerHTML = '<div class="empty-state">No suggestions yet. Run indexing, triage, or profile building first.</div>';
    return;
  }
  target.innerHTML = items.map(item => `
    <div class="insights-action ${escapeHtml(item.priority || 'medium')}">
      <span>${escapeHtml(item.kind || 'suggestion')}</span>
      <strong>${escapeHtml(item.title || '')}</strong>
      <p>${escapeHtml(item.detail || '')}</p>
      <button class="btn" onclick="openInsightsTarget('${escapeHtml(item.kind || '')}')">${escapeHtml(item.action || 'Open')}</button>
    </div>
  `).join('');
}

function renderInsightsFileOrg(items) {
  const target = document.getElementById('insightsFileOrg');
  if (!target) return;
  if (!items.length) {
    target.innerHTML = '<div class="empty-state">No folder recommendations yet.</div>';
    return;
  }
  target.innerHTML = `<table class="data-table">
    <thead><tr><th>Directory</th><th>Recommendation</th><th>Files</th><th>Signals</th></tr></thead>
    <tbody>${items.map(item => `
      <tr>
        <td class="path" title="${escapeHtml(item.prefix || item.directory)}">${escapeHtml(item.directory || '')}</td>
        <td><span class="type-badge insights-rec-${escapeHtml(item.recommendation || 'review')}">${escapeHtml(item.recommendation || 'review')}</span><br><span class="muted">${escapeHtml(item.reason || '')}</span></td>
        <td>${Number(item.total || 0).toLocaleString()}<br><span class="muted">${formatBytes(item.total_size || 0)}</span></td>
        <td class="muted">config ${item.config || 0}, data ${item.data || 0}, generated ${item.generated || 0}</td>
      </tr>
    `).join('')}</tbody>
  </table>`;
}

function renderInsightsCleanup(items) {
  const target = document.getElementById('insightsCleanup');
  if (!target) return;
  if (!items.length) {
    target.innerHTML = '<div class="empty-state">No cleanup candidates. Kith is intentionally conservative here.</div>';
    return;
  }
  target.innerHTML = `<table class="data-table">
    <thead><tr><th>Path</th><th>Risk</th><th>Why</th><th>Size</th><th>Modified</th></tr></thead>
    <tbody>${items.map(item => `
      <tr>
        <td class="path" title="${escapeHtml(item.full_path || item.path)}">${escapeHtml(item.path || '')}<br><span class="muted">${escapeHtml(item.action || '')}</span></td>
        <td><span class="type-badge insights-risk-${escapeHtml(item.risk || 'medium')}">${escapeHtml(item.risk || 'medium')}</span></td>
        <td>${escapeHtml(item.reason || '')}</td>
        <td>${formatBytes(item.size_bytes || 0)}</td>
        <td>${formatTime(item.modified_at)}</td>
      </tr>
    `).join('')}</tbody>
  </table>`;
}

function renderInsightsInterests(videoItems, webInterests) {
  const target = document.getElementById('insightsInterests');
  if (!target) return;
  const topics = webInterests.topics || [];
  const domains = webInterests.top_domains || [];
  target.innerHTML = `
    <div class="insights-interest-block">
      <h4>Video</h4>
      ${videoItems.length ? videoItems.map(item => `
        <div class="insights-mini-row">
          <strong>${escapeHtml(item.domain || '')}</strong>
          <span>${Number(item.count || 0).toLocaleString()} signals · ${formatTime(item.last_seen)}</span>
        </div>
      `).join('') : '<div class="empty-state">No video-domain signals yet.</div>'}
    </div>
    <div class="insights-interest-block">
      <h4>Topics</h4>
      <div class="insights-topic-cloud">
        ${topics.slice(0, 16).map(item => `<span>${escapeHtml(item.topic || '')}</span>`).join('') || '<span>No topics yet</span>'}
      </div>
    </div>
    <div class="insights-interest-block">
      <h4>Top Domains</h4>
      ${domains.slice(0, 12).map(item => `
        <div class="insights-mini-row">
          <strong>${escapeHtml(item.domain || '')}</strong>
          <span>${escapeHtml(item.kind || 'web')} · ${Number(item.count || 0).toLocaleString()}</span>
        </div>
      `).join('') || '<div class="empty-state">No browser signals yet.</div>'}
    </div>
  `;
}

function openInsightsTarget(kind) {
  const target = kind === 'memory' ? 'summary' : (kind === 'privacy' ? 'llm' : 'triage');
  const tab = document.querySelector(`.nav-tab[data-tab="${target}"]`);
  if (tab) tab.click();
}
