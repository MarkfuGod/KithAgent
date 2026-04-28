import { app, BrowserWindow, ipcMain, shell } from 'electron';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createDaemonBridge, DEFAULT_DAEMON_BASE_URL } from './daemon.js';
import { runFrontendChat } from './frontendChat.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '../../..');
const daemon = createDaemonBridge({ repoRoot });
const LONG_SYSCALL_TIMEOUT_MS = 180000;
const ONBOARDING_SYSCALL_TIMEOUT_MS = 600000;
const DASHBOARD_BASE_URL = 'http://127.0.0.1:7438';
const DEFAULT_CHAT_PATH = '/chat/completions';
const MODEL_SETTINGS_GET_SCRIPT = `
import json
from src.kernel.user_settings import load_model_settings
print(json.dumps(load_model_settings(), ensure_ascii=False))
`;
const MODEL_SETTINGS_SAVE_SCRIPT = `
import json
import sys
from src.kernel.user_settings import save_model_settings
payload = json.loads(sys.argv[1] or "{}")
print(json.dumps(save_model_settings(payload), ensure_ascii=False))
`;
const DESKTOP_RUNTIME_MODEL_SCRIPT = `
import json
from src.kernel.user_settings import load_desktop_runtime_model_settings
print(json.dumps(load_desktop_runtime_model_settings(), ensure_ascii=False))
`;
const TRIAGE_FILES_SCRIPT = `
import json
import sqlite3
import sys
from pathlib import Path

prefix = str(sys.argv[1] or '').strip()
limit = max(1, min(int(sys.argv[2] or '200'), 300))
home = str(Path.home())
if prefix == '~':
    normalized_prefix = home
elif prefix.startswith('~/'):
    normalized_prefix = str(Path(home) / prefix[2:])
else:
    normalized_prefix = prefix

db_path = Path.home() / '.agent_sys' / 'memory.db'
db = sqlite3.connect(str(db_path), check_same_thread=False)
try:
    total = db.execute(
        'SELECT COUNT(*) FROM file_index WHERE path = ? OR path LIKE ?',
        (normalized_prefix, f'{normalized_prefix}/%'),
    ).fetchone()[0]
    rows = db.execute(
        """SELECT path, file_type, size_bytes,
                  CASE WHEN triage_status = '' OR triage_status IS NULL THEN 'untriaged'
                       ELSE triage_status END as status
           FROM file_index
           WHERE path = ? OR path LIKE ?
           ORDER BY
             CASE
               WHEN triage_status = '' OR triage_status IS NULL THEN 0
               WHEN triage_status = 'untriaged' THEN 0
               WHEN triage_status = 'unknown' THEN 1
               WHEN triage_status = 'skip' THEN 2
               WHEN triage_status = 'low' THEN 3
               WHEN triage_status = 'medium' THEN 4
               WHEN triage_status = 'high' THEN 5
               ELSE 6
             END,
             size_bytes DESC,
             path ASC
           LIMIT ?""",
        (normalized_prefix, f'{normalized_prefix}/%', limit),
    ).fetchall()
finally:
    db.close()

def display_path(path):
    if path.startswith(home):
        rel = path[len(home):].strip('/')
        return f'~/{rel}' if rel else '~'
    return path

print(json.dumps({
    'prefix': display_path(normalized_prefix),
    'total_files': total,
    'limit': limit,
    'files': [
        {
            'path': display_path(path),
            'file_type': (file_type or '').lower() or '(no suffix)',
            'size_bytes': size or 0,
            'status': status,
        }
        for path, file_type, size, status in rows
    ],
}))
`;

let mainWindow = null;
let eventAbortController = null;

function normalizeApiBaseUrl(rawHost, rawPath = DEFAULT_CHAT_PATH) {
  let host = String(rawHost || '').trim().replace(/\/+$/, '');
  const path = String(rawPath || '').trim();
  if (!host) return '';
  if (path && path !== DEFAULT_CHAT_PATH && !host.endsWith(path.replace(/\/+$/, ''))) {
    host = `${host}/${path.replace(/^\/+/, '')}`.replace(/\/+$/, '');
  }
  for (const suffix of ['/chat/completions', '/responses', '/completions']) {
    if (host.endsWith(suffix)) {
      return host.slice(0, -suffix.length).replace(/\/+$/, '');
    }
  }
  return host;
}

function sendDaemonEvent(event) {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed()) {
    return;
  }
  mainWindow.webContents.send('daemon:event', event);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1220,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    title: 'Kith',
    backgroundColor: '#080b10',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 18, y: 18 },
    webPreferences: {
      preload: join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else if (!app.isPackaged) {
    mainWindow.loadURL('http://127.0.0.1:5173');
  } else {
    mainWindow.loadFile(join(repoRoot, 'desktop/dist/renderer/index.html'));
  }
}

ipcMain.handle('daemon:status', daemon.daemonStatus);
ipcMain.handle('daemon:start', daemon.ensureDaemon);
ipcMain.handle('daemon:stop', () => daemon.runCli(['stop']).then(() => ({ stopped: true })));
ipcMain.handle('daemon:openDashboard', async () => {
  const dashboard = await daemon.ensureDashboard();
  const opened = await shell.openExternal(dashboard.url);
  return { ...dashboard, opened };
});

async function fetchDashboardJson(path, init = {}) {
  await daemon.ensureDashboard();
  const response = await fetch(`${DASHBOARD_BASE_URL}${path}`, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Dashboard HTTP ${response.status}`);
  }
  return payload;
}

ipcMain.handle('triage:clusters', async (_event, payload = {}) => {
  const depth = Number(payload.depth || 3);
  const limit = Number(payload.limit || 80);
  return fetchDashboardJson(`/api/file-clusters?depth=${encodeURIComponent(depth)}&limit=${encodeURIComponent(limit)}`);
});

ipcMain.handle('triage:files', async (_event, payload = {}) => {
  const prefix = String(payload.prefix || '');
  const limit = Number(payload.limit || 200);
  return daemon.runPythonJson(TRIAGE_FILES_SCRIPT, [prefix, limit]);
});

ipcMain.handle('triage:clusterDecision', async (_event, payload = {}) => {
  return fetchDashboardJson('/api/file-clusters/decision', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Kith-Dashboard': '1',
    },
    body: JSON.stringify(payload),
  });
});
ipcMain.handle('daemon:events:start', async () => {
  await daemon.ensureDaemon();
  if (eventAbortController) {
    return { started: true };
  }

  eventAbortController = new AbortController();
  const token = daemon.readAuthToken();
  const headers = token ? { 'X-Agent-Token': token } : {};

  fetch(`${DEFAULT_DAEMON_BASE_URL}/events`, {
    headers,
    signal: eventAbortController.signal,
  }).then(async (response) => {
    if (!response.ok || !response.body) {
      throw new Error(`events stream failed: HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const event = parseSseEvent(chunk);
        if (event) {
          sendDaemonEvent(event);
        }
      }
    }
  }).catch((error) => {
    if (error.name !== 'AbortError') {
      sendDaemonEvent({
        type: 'desktop.events.error',
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }).finally(() => {
    eventAbortController = null;
  });

  return { started: true };
});
ipcMain.handle('daemon:events:stop', async () => {
  eventAbortController?.abort();
  eventAbortController = null;
  return { stopped: true };
});

function parseSseEvent(chunk) {
  const lines = chunk.split('\n');
  let type = 'message';
  let data = '';
  for (const line of lines) {
    if (line.startsWith('event:')) {
      type = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      data += line.slice(5).trim();
    }
  }
  if (!data) {
    return null;
  }
  try {
    return { type, data: JSON.parse(data) };
  } catch {
    return { type, data };
  }
}

ipcMain.handle('kith:chat', async (_event, payload) => {
  return runFrontendChat({ daemon, payload, sendEvent: sendDaemonEvent });
});

ipcMain.handle('insights:get', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('assistant.insights', payload, 1);
});

ipcMain.handle('onboarding:bootstrap', async (_event, payload = {}) => {
  const started = Date.now();
  console.info('[desktop] onboarding:bootstrap received', {
    includeBrowserHistory: Boolean(payload.include_browser_history),
    historyLimit: payload.history_limit,
  });
  await daemon.ensureDaemon();
  console.info('[desktop] onboarding:bootstrap daemon ready');
  try {
    const result = await daemon.syscall('onboarding.bootstrap', payload, 1, {
      timeoutMs: ONBOARDING_SYSCALL_TIMEOUT_MS,
      unixTimeoutMs: ONBOARDING_SYSCALL_TIMEOUT_MS,
    });
    console.info('[desktop] onboarding:bootstrap completed', {
      elapsedMs: Date.now() - started,
      browserEntries: result?.browser_history?.entries_count,
      facts: result?.profile_facts?.length,
    });
    return result;
  } catch (error) {
    console.error('[desktop] onboarding:bootstrap failed', error);
    throw error;
  }
});

ipcMain.handle('profile:summary', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('profile.summary', payload, 1, { timeoutMs: LONG_SYSCALL_TIMEOUT_MS });
});

ipcMain.handle('memory:review', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('memory.review', payload, 1);
});

ipcMain.handle('memory:feedback', async (_event, payload) => {
  await daemon.ensureDaemon();
  return daemon.syscall('memory.feedback', payload, 1);
});

ipcMain.handle('sources:get', async () => {
  await daemon.ensureDaemon();
  return daemon.syscall('sources.get', {}, 1, { httpTimeoutMs: 8000, unixTimeoutMs: 12000 });
});

ipcMain.handle('sources:configure', async (_event, payload) => {
  await daemon.ensureDaemon();
  return daemon.syscall('sources.configure', payload, 0, { httpTimeoutMs: 10000, unixTimeoutMs: 20000 });
});

ipcMain.handle('settings:model', async (_event, payload) => {
  if (payload?.scope === 'desktop') {
    return daemon.runPythonJson(MODEL_SETTINGS_SAVE_SCRIPT, [JSON.stringify(payload)]);
  }
  await daemon.ensureDaemon();
  return daemon.syscall('settings.model', payload, 0, { httpTimeoutMs: 10000, unixTimeoutMs: 20000 });
});

ipcMain.handle('settings:model:get', async () => {
  return daemon.runPythonJson(MODEL_SETTINGS_GET_SCRIPT);
});

ipcMain.handle('settings:model:list', async (_event, payload = {}) => {
  const baseUrl = normalizeApiBaseUrl(payload.base_url || payload.api_host, payload.api_path);
  if (!baseUrl) {
    throw new Error('请先填写 provider API 主机。');
  }

  const apiKeyEnv = String(payload.api_key_env || '');
  let apiKey = String(payload.api_key || (apiKeyEnv ? process.env[apiKeyEnv] : '') || '').trim();
  if (!apiKey && payload.scope === 'desktop') {
    const savedDesktop = await daemon.runPythonJson(DESKTOP_RUNTIME_MODEL_SCRIPT).catch(() => ({}));
    apiKey = String(savedDesktop.api_key || '').trim();
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error('model list request timed out')), 15000);
  try {
    const response = await fetch(`${baseUrl}/models`, {
      headers: {
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error?.message || data.error || `HTTP ${response.status}`);
    }
    const rawModels = Array.isArray(data.data) ? data.data : Array.isArray(data.models) ? data.models : [];
    const models = rawModels
      .map((item) => (typeof item === 'string' ? item : item?.id || item?.name))
      .filter(Boolean)
      .map(String);
    return { models };
  } finally {
    clearTimeout(timeout);
  }
});

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
