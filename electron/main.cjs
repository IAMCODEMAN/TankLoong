const { app, BrowserWindow, ipcMain, dialog, shell, protocol } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { Backend } = require('./bridge.cjs');
const { APP_URL, createAssetHandler, validateAssets } = require('./assets.cjs');

protocol.registerSchemesAsPrivileged([{ scheme: 'tankloong', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: true
} }]);

let window, backend, outputDirectory = '', searchBusy = false, jobs = 0, quitting = false;
const completed = new Set();
const root = app.getAppPath();
const settingsPath = () => path.join(app.getPath('userData'), 'settings.json');
const startupLogPath = () => path.join(app.getPath('userData'), 'startup.log');
async function startupLog(event, detail) {
  try {
    await fs.mkdir(app.getPath('userData'), { recursive: true });
    await fs.appendFile(startupLogPath(), JSON.stringify({ time: new Date().toISOString(), event, detail }) + '\n', 'utf8');
  } catch { /* Diagnostics must not replace the original startup error. */ }
}

function assertSender(event) {
  if (event.sender !== window?.webContents || event.senderFrame !== window.webContents.mainFrame) {
    throw new Error('无效的应用请求。');
  }
}
function handle(channel, callback) {
  ipcMain.handle(channel, async (event, ...args) => {
    assertSender(event);
    return callback(...args);
  });
}
async function createWindow() {
  await fs.mkdir(app.getPath('userData'), { recursive: true });
  const uiDirectory = path.join(root, 'ui');
  await startupLog('starting', { version: app.getVersion(), appPath: root });
  await validateAssets(uiDirectory);
  protocol.handle('tankloong', createAssetHandler(uiDirectory, error => startupLog('asset-error', error.message)));
  const backendData = path.join(app.getPath('userData'), 'backend');
  await fs.mkdir(backendData, { recursive: true });
  const command = app.isPackaged ? path.join(process.resourcesPath, 'backend', 'tankloong-backend.exe') : path.join(root, '.venv', 'Scripts', 'python.exe');
  const args = app.isPackaged ? [] : ['-u', path.join(root, 'src', 'backend.py')];
  backend = new Backend(command, args, { cwd: backendData, env: {
    ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1',
    TANKLOONG_DATA_DIR: backendData, WIN_PD_OVERRIDE_LOCAL_APPDATA: backendData
  } });
  backend.on('event', event => {
    if (event.event === 'download' && event.state === 'done') completed.add(event.outputPath);
    if (window && !window.isDestroyed()) window.webContents.send('backend:event', event);
  });

  window = new BrowserWindow({
    width: 1280, height: 900, minWidth: 940, minHeight: 720, show: false,
    backgroundColor: '#090d19', title: 'TankLoong', icon: path.join(root, 'assets', 'tankloong.ico'),
    frame: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    if (isMainFrame) startupLog('load-failed', { code, description, url });
  });
  window.webContents.on('render-process-gone', (_event, details) => startupLog('renderer-exited', details));
  window.once('ready-to-show', () => window.show());
  window.on('close', event => {
    if (!quitting && jobs > 0) {
      event.preventDefault();
      dialog.showMessageBox(window, { type: 'question', title: '下载仍在进行', message: '关闭将中断当前下载，确定退出吗？', buttons: ['继续下载', '退出'], defaultId: 0, cancelId: 0 }).then(({ response }) => {
        if (response === 1) { quitting = true; app.quit(); }
      });
    }
  });
  await window.loadURL(APP_URL);
  await startupLog('ui-loaded', APP_URL);
}

handle('app:initial', async () => {
  let saved = '';
  try { saved = JSON.parse(await fs.readFile(settingsPath(), 'utf8')).directory || ''; } catch {}
  return { savedDirectory: saved, version: app.getVersion(), ...(await backend.request('health')) };
});
handle('directory:choose', async () => {
  if (searchBusy || jobs) throw new Error('请等待当前搜索或下载结束后再更换目录。');
  const result = await dialog.showOpenDialog(window, { title: '选择 MP3 下载目录', defaultPath: outputDirectory || app.getPath('music'), properties: ['openDirectory', 'createDirectory'] });
  if (result.canceled || !result.filePaths[0]) return null;
  const directory = path.resolve(result.filePaths[0]);
  await backend.request('set_directory', { directory });
  outputDirectory = directory;
  await fs.writeFile(settingsPath(), JSON.stringify({ directory }), 'utf8');
  return directory;
});
handle('music:search', async (keyword, searchId) => {
  if (!outputDirectory) throw new Error('请先选择下载目录。');
  if (searchBusy) throw new Error('搜索正在进行中。');
  if (typeof keyword !== 'string' || !keyword.trim() || keyword.length > 200) throw new Error('请输入 1–200 个字符的歌曲名称。');
  if (typeof searchId !== 'string' || searchId.length > 100) throw new Error('无效的搜索标识。');
  searchBusy = true;
  try { return await backend.request('search', { keyword: keyword.trim(), searchId }, 55000); }
  finally { searchBusy = false; }
});
handle('music:download', async (resultId, downloadId) => {
  if (!outputDirectory) throw new Error('请先选择下载目录。');
  if (typeof resultId !== 'string' || resultId.length > 100) throw new Error('无效的歌曲。');
  if (typeof downloadId !== 'string' || downloadId.length > 100) throw new Error('无效的下载标识。');
  jobs++;
  try { return await backend.request('download', { resultId, downloadId }, 15 * 60 * 1000); }
  finally { jobs--; }
});
handle('directory:open', () => outputDirectory ? shell.openPath(outputDirectory) : undefined);
handle('file:reveal', file => {
  if (!completed.has(file)) throw new Error('文件尚未下载完成。');
  shell.showItemInFolder(file);
});
handle('window:control', action => {
  if (action === 'minimize') window.minimize();
  if (action === 'maximize') window.isMaximized() ? window.unmaximize() : window.maximize();
  if (action === 'close') window.close();
});
app.whenReady().then(createWindow).catch(async error => {
  await startupLog('startup-failed', error.stack || error.message);
  dialog.showErrorBox('TankLoong 启动失败', `${error.message}\n\n启动日志：${startupLogPath()}`);
  app.quit();
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { if (quitting || !jobs) backend?.stop(); });
