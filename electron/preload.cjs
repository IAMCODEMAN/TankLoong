const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('tankloong', {
  initial: () => ipcRenderer.invoke('app:initial'),
  chooseDirectory: () => ipcRenderer.invoke('directory:choose'),
  search: (keyword, searchId) => ipcRenderer.invoke('music:search', keyword, searchId),
  download: (id, downloadId) => ipcRenderer.invoke('music:download', id, downloadId),
  openDirectory: () => ipcRenderer.invoke('directory:open'),
  reveal: file => ipcRenderer.invoke('file:reveal', file),
  windowControl: action => ipcRenderer.invoke('window:control', action),
  onEvent: callback => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on('backend:event', listener);
    return () => ipcRenderer.removeListener('backend:event', listener);
  }
});
