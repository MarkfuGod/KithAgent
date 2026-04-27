import { contextBridge, ipcRenderer } from 'electron';

const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload);

contextBridge.exposeInMainWorld('kith', {
  daemon: {
    status: () => invoke('daemon:status'),
    start: () => invoke('daemon:start'),
    stop: () => invoke('daemon:stop'),
    openDashboard: () => invoke('daemon:openDashboard'),
  },
  jarvis: {
    chat: (payload) => invoke('jarvis:chat', payload),
  },
  profile: {
    summary: (payload) => invoke('profile:summary', payload),
  },
  memory: {
    review: (payload) => invoke('memory:review', payload),
    feedback: (payload) => invoke('memory:feedback', payload),
  },
  sources: {
    get: () => invoke('sources:get'),
    configure: (payload) => invoke('sources:configure', payload),
  },
  settings: {
    model: (payload) => invoke('settings:model', payload),
  },
});
