// Launch a real Electron window. Backend health and directory validation use
// Python; deterministic song IPC fixtures cover UI states without a provider.
const { _electron: electron } = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
(async () => {
  await fs.mkdir('artifacts', { recursive: true });
  const temp = await fs.mkdtemp(path.resolve('artifacts/ui-'));
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const packaged = process.argv.includes('--packaged');
  const application = await electron.launch({ executablePath: packaged ? path.resolve('release/win-unpacked/TankLoong.exe') : require('electron'), args: [...(packaged ? [] : [path.resolve('.')]), ...(process.argv.includes('--no-sandbox') ? ['--no-sandbox'] : []), `--user-data-dir=${path.join(temp, 'app')}`], env });
  try {
    const page = await application.firstWindow();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.getByText('音乐服务已就绪', { exact: true }).waitFor({ timeout: 60000 });
    assert.equal(await page.locator('#keyword').isDisabled(), true);
    assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
    await fs.mkdir('docs/screenshots', { recursive: true });
    await page.screenshot({ path: 'docs/screenshots/tankloong-electron.png' });
    // Emulate only native OS dialog selection, keeping real IPC validation.
    await application.evaluate(({ dialog }, directory) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [directory] });
    }, path.join(temp, '音乐下载'));
    await page.locator('#choose-directory').click();
    await page.waitForFunction(() => !document.querySelector('#keyword').disabled);
    await page.locator('#keyword').fill('UI 验证样例');
    await application.evaluate(({ ipcMain, BrowserWindow }) => {
      ipcMain.removeHandler('music:search');
      ipcMain.handle('music:search', async (_event, _keyword, searchId) => {
        BrowserWindow.getAllWindows()[0].webContents.send('backend:event', {
          event: 'search-source', searchId, results: [
            { id: 'test', title: 'UI 验证样例 · 非真实搜索结果', artist: '测试', album: '验证', duration: '03:20', source: 'QQ', downloadable: true },
            { id: 'unavailable', title: '<script>不应作为 HTML 执行</script>', artist: '不可用来源', album: '-', duration: '-', source: 'Netease', downloadable: false }
          ]
        });
        return { diagnostics: [] };
      });
      ipcMain.removeHandler('music:download');
      let attempts = 0;
      ipcMain.handle('music:download', async (_event, resultId, downloadId) => {
        if (++attempts === 1) throw new Error('UI 测试：模拟网络中断');
        // Deliberately arrive after the completion response: must not regress.
        setTimeout(() => BrowserWindow.getAllWindows()[0].webContents.send('backend:event', { event: 'download', resultId, downloadId, state: 'converting' }), 100);
        return { outputPath: 'UI 测试目录/验证音频.mp3' };
      });
    });
    await page.locator('#search-button').click();
    try { await page.waitForFunction(() => document.querySelectorAll('.track').length === 2, null, { timeout: 10000 }); }
    catch (error) { console.log('UI DEBUG', await page.locator('body').innerText(), errors); throw error; }
    assert.equal(await page.locator('.track-download').nth(1).isDisabled(), true);
    assert.equal(await page.locator('#track-list script').count(), 0);
    await page.locator('.track-download').first().click();
    await page.getByText('下载失败', { exact: true }).waitFor();
    await page.getByText('重试下载 ↻', { exact: true }).click();
    await page.getByText('已完成', { exact: true }).waitFor();
    await page.waitForTimeout(200);
    await page.getByText('已完成', { exact: true }).waitFor();
    await page.screenshot({ path: 'docs/screenshots/tankloong-electron-test-states.png' });
    assert.deepEqual(errors, []);
    // Smaller supported window: ensure no horizontal page overflow.
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(940, 720));
    await page.waitForTimeout(200);
    await page.screenshot({ path: 'docs/screenshots/tankloong-electron-small-test.png' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    console.log('Electron UI passed: real health, directory gate, search, escaping, failure, retry, success, 940px layout.');
  } finally { await application.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
