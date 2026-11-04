// Run the shipped executable without Playwright, a debugger, or --no-sandbox.
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  await fs.mkdir('artifacts', { recursive: true });
  const data = await fs.mkdtemp(path.resolve('artifacts/native-startup-'));
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const executable = path.resolve('release/win-unpacked/TankLoong.exe');
  const extra = process.argv.includes('--disable-gpu') ? ['--disable-gpu'] : [];
  const child = spawn(executable, [`--user-data-dir=${data}`, ...extra], { env, cwd: data, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: false });
  let stderr = '', exitCode;
  child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-8000); });
  child.on('exit', code => { exitCode = code; });
  try {
    const deadline = Date.now() + 20000;
    let records = [];
    while (Date.now() < deadline && exitCode === undefined) {
      try { records = (await fs.readFile(path.join(data, 'startup.log'), 'utf8')).trim().split('\n').map(JSON.parse); } catch {}
      if (records.some(record => ['ui-loaded', 'startup-failed'].includes(record.event))) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    const report = { executable, data, noSandbox: false, extra, exitCode, records, stderr };
    await fs.writeFile(path.resolve('artifacts/native-startup-result.json'), JSON.stringify(report, null, 2));
    assert.ok(records.some(record => record.event === 'ui-loaded'), JSON.stringify(report, null, 2));
    assert.equal(records.some(record => record.event === 'load-failed' || record.event === 'startup-failed'), false);
    console.log('Normal packaged startup passed: default sandbox, no debugger, independent cwd.');
  } finally {
    if (exitCode === undefined && child.pid) {
      try { execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); } catch {}
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
