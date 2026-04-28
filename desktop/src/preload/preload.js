import { contextBridge, ipcRenderer } from 'electron';

const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload);

contextBridge.exposeInMainWorld('kith', {
  daemon: {
    status: () => invoke('daemon:status'),
    start: () => invoke('daemon:start'),
    stop: () => invoke('daemon:stop'),
    openDashboard: () => invoke('daemon:openDashboard'),
    events: {
      start: () => invoke('daemon:events:start'),
      stop: () => invoke('daemon:events:stop'),
      onEvent: (callback) => {
        const listener = (_event, payload) => callback(payload);
        ipcRenderer.on('daemon:event', listener);
        return () => ipcRenderer.removeListener('daemon:event', listener);
      },
    },
  },
  assistant: {
    chat: (payload) => invoke('kith:chat', payload),
  },
  insights: {
    get: (payload) => invoke('insights:get', payload),
  },
  onboarding: {
    bootstrap: (payload) => invoke('onboarding:bootstrap', payload),
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
    modelGet: () => invoke('settings:model:get'),
    modelList: (payload) => invoke('settings:model:list', payload),
  },
  triage: {
    clusters: (payload) => invoke('triage:clusters', payload),
    files: (payload) => invoke('triage:files', payload),
    clusterDecision: (payload) => invoke('triage:clusterDecision', payload),
  },
});
