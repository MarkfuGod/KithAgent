/**
 * files.js — Files & Directories tab: search input + directory composition bars.
 */

let searchTimer = null;

async function loadDirectories() {
  const data = await fetch(API + '/api/directories?depth=2').then(r => r.json());
  const container = document.getElementById('dirTree');
  if (data.length === 0) {
    container.innerHTML = '<div class="empty-state">No directory data</div>';
    return;
  }
  const maxTotal = Math.max(...data.map(d => d.total));
  container.innerHTML = data.slice(0, 40).map(d => {
    const cats = ['code', 'document', 'image', 'data', 'config', 'other'];
    const bars = cats.map(c => {
      const pct = d.total > 0 ? (d[c] / d.total * 100) : 0;
      return pct > 0 ? `<div class="bar-segment bar-${c}" style="width:${pct}%" title="${c}: ${d[c]}"></div>` : '';
    }).join('');
    return `
      <div class="dir-bar">
        <div class="name" title="${d.directory}">~/${d.directory}</div>
        <div class="bar-container" style="max-width:${d.total / maxTotal * 100}%">${bars}</div>
        <div class="count-label">${d.total.toLocaleString()}</div>
      </div>
    `;
  }).join('');
}

function debounceSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doSearch, 300);
}

async function doSearch() {
  const q = document.getElementById('fileSearch').value.trim();
  const container = document.getElementById('searchResults');
  if (q.length < 2) {
    container.style.display = 'none';
    return;
  }
  const data = await fetch(API + '/api/search?q=' + encodeURIComponent(q)).then(r => r.json());
  container.style.display = 'block';
  if (data.length === 0) {
    container.innerHTML = '<div class="data-card"><div style="padding:20px;text-align:center;color:var(--text-muted);">No results for "' + escapeHtml(q) + '"</div></div>';
    return;
  }
  container.innerHTML = `
    <div class="data-card">
      <div class="card-header"><h3>${data.length} results for "${escapeHtml(q)}"</h3></div>
      <div style="max-height:400px;overflow-y:auto;">
        <table class="data-table">
          <thead><tr><th>Path</th><th>Type</th><th>Size</th><th>Summary</th></tr></thead>
          <tbody>${data.map(f => `
            <tr>
              <td class="path">${f.path}</td>
              <td><span class="type-badge">${f.type || '?'}</span></td>
              <td>${formatBytes(f.size)}</td>
              <td style="max-width:300px">${f.summary || '-'}</td>
            </tr>
          `).join('')}</tbody>
        </table>
      </div>
    </div>
  `;
}
