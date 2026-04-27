import { app, BrowserWindow, ipcMain, shell } from 'electron';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createDaemonBridge } from './daemon.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '../../..');
const daemon = createDaemonBridge({ repoRoot });

let mainWindow = null;

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
ipcMain.handle('daemon:openDashboard', () => shell.openExternal('http://127.0.0.1:7438'));

ipcMain.handle('jarvis:chat', async (_event, payload) => {
  await daemon.ensureDaemon();
  return daemon.syscall('assistant.chat', payload, 1);
});

ipcMain.handle('profile:summary', async (_event, payload = {}) => {
  await daemon.ensureDaemon();
  return daemon.syscall('profile.summary', payload, 1);
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
  return daemon.syscall('sources.get', {}, 1);
});

ipcMain.handle('sources:configure', async (_event, payload) => {
  await daemon.ensureDaemon();
  return daemon.syscall('sources.configure', payload, 0);
});

ipcMain.handle('settings:model', async (_event, payload) => {
  await daemon.ensureDaemon();
  return daemon.syscall('settings.model', payload, 0);
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
