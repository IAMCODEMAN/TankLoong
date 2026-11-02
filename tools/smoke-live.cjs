// Optional network-dependent validation of the packaged app. Uses real songs,
// real musicdl and real FFmpeg. Output is isolated under artifacts/.
const { _electron: electron } = require('playwright-core');
const path = require('node:path');
const fs = require('node:fs/promises');
const { execFileSync } = require('node:child_process');
(async () => {
  await fs.mkdir('artifacts', { recursive: true });
  const temporary = await fs.mkdtemp(path.resolve('artifacts/release-live-'));
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const application = await electron.launch({ executablePath: path.resolve('release/win-unpacked/TankLoong.exe'),
    args: ['--no-sandbox', `--user-data-dir=${path.join(temporary, 'app')}`], env });
  try {
    const page = await application.firstWindow();
    await page.getByText('音乐服务已就绪', { exact: true }).waitFor({ timeout: 60000 });
    const output = path.join(temporary, 'MP3'); await fs.mkdir(output);
    await application.evaluate(({ dialog }, directory) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [directory] }); }, output);
    await page.locator('#choose-directory').click();
    await page.waitForFunction(() => !document.querySelector('#keyword').disabled);
    await page.locator('#keyword').fill('晴天');
    await page.locator('#search-button').click();
    await page.waitForFunction(() => !document.querySelector('#search-button').disabled, null, { timeout: 60000 });
    const available = page.locator('.track-download:not([disabled])');
    console.log('REAL_SEARCH_RESULTS', await page.locator('.track').count(), 'DOWNLOADABLE', await available.count());
    if (!await available.count()) throw new Error('No downloadable songs returned from live providers.');
    await available.first().click();
    await page.waitForFunction(() => document.querySelector('.download-item.done,.download-item.failed'), null, { timeout: 150000 });
    const failure = page.locator('.download-item.failed');
    if (await failure.count()) throw new Error(await failure.innerText());
    const files = await fs.readdir(output);
    const mp3 = files.find(file => file.endsWith('.mp3'));
    if (!mp3) throw new Error('MP3 not created.');
    const file = path.join(output, mp3);
    execFileSync(path.resolve('release/win-unpacked/resources/backend/_internal/ffmpeg/bin/ffmpeg.exe'), ['-v', 'error', '-i', file, '-f', 'null', '-'], { windowsHide: true, timeout: 30000 });
    await page.screenshot({ path: 'docs/screenshots/tankloong-electron-live.png' });
    console.log('RELEASE_END_TO_END_PASSED', file, (await fs.stat(file)).size, 'bytes; full decode succeeded.');
  } finally {
    await application.evaluate(({ dialog }) => { dialog.showMessageBox = async () => ({ response: 1 }); }).catch(() => {});
    await application.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
