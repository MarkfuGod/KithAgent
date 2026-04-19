/**
 * common.js — shared globals + utility helpers used by every tab module.
 *
 * Loaded first. Exposes: API, chartColors, formatBytes, formatTime,
 * formatFullTime, escapeHtml, showToast. Also configures Chart.js defaults
 * if the library loaded.
 */

const API = '';

const chartColors = [
  '#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff', '#f778ba',
  '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#d2a8ff', '#ff9bce',
  '#a5d6ff', '#7ee787', '#f0c844', '#ffa198', '#e8d4ff', '#ffb8e0',
];

if (typeof Chart !== 'undefined') {
  Chart.defaults.color = '#8b949e';
  Chart.defaults.borderColor = '#30363d';
  Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
} else {
  console.warn('Chart.js not loaded (CDN unreachable?) — charts will be skipped');
}

function formatBytes(b) {
  if (!b || b === 0) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(b) / Math.log(1024));
  return (b / Math.pow(1024, i)).toFixed(1) + ' ' + u[i];
}

function formatTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return Math.floor(diff) + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatFullTime(ts) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString();
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function showToast(msg, type) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.className = 'toast ' + (type || '');
  setTimeout(() => { el.className = 'toast'; }, 4000);
}

async function reloadDaemonConfig() {
  try {
    const resp = await fetch(API + '/api/reload-config', { method: 'POST' }).then(r => r.json());
    if (resp.success) {
      showToast('Config applied to running daemon.', 'success');
      return true;
    }
    showToast('Reload failed: ' + (resp.error || 'unknown'), 'error');
    return false;
  } catch (e) {
    showToast('Daemon not reachable — restart manually to apply.', 'error');
    return false;
  }
}
