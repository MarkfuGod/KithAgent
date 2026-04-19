/**
 * knowledge.js — Knowledge Base tab: category sidebar + expandable entry list.
 */

let knowledgeData = null;

async function loadKnowledge() {
  knowledgeData = await fetch(API + '/api/knowledge?limit=100').then(r => r.json());
  const catEl = document.getElementById('knowledgeCats');
  catEl.innerHTML = `
    <div class="cat-item active" onclick="showKnowledgeCategory(null, this)">
      <span>All</span>
      <span class="count">${knowledgeData.entries.length}</span>
    </div>
    ${knowledgeData.categories.map(c => `
      <div class="cat-item" onclick="showKnowledgeCategory('${c.name}', this)">
        <span>${c.name}</span>
        <span class="count">${c.count}</span>
      </div>
    `).join('')}
  `;
  showKnowledgeCategory(null, catEl.querySelector('.cat-item'));
}

async function showKnowledgeCategory(cat, el) {
  document.querySelectorAll('.cat-item').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');

  let data;
  if (cat) {
    data = await fetch(API + '/api/knowledge?category=' + encodeURIComponent(cat) + '&limit=30').then(r => r.json());
  } else {
    data = knowledgeData;
  }

  const content = document.getElementById('knowledgeContent');
  if (!data.entries.length) {
    content.innerHTML = '<div class="empty-state">No entries in this category</div>';
    return;
  }

  content.innerHTML = data.entries.map((e, i) => {
    const contentStr = typeof e.content === 'object' ? JSON.stringify(e.content, null, 2) : e.content;
    return `
      <div class="knowledge-entry">
        <div class="entry-header" onclick="this.nextElementSibling.classList.toggle('open')">
          <span class="entry-id">${e.id}</span>
          <span class="entry-time">${formatFullTime(e.updated_at)}</span>
        </div>
        <div class="entry-body${i === 0 ? ' open' : ''}">
          <pre>${escapeHtml(contentStr)}</pre>
        </div>
      </div>
    `;
  }).join('');
}
