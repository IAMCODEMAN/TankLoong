const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { Backend } = require('../electron/bridge.cjs');
test('real Python bridge: health, request validation and Unicode directory', async () => {
  fs.mkdirSync('artifacts', { recursive: true });
  const temporary = fs.mkdtempSync(path.resolve('artifacts/bridge-'));
  const backend = new Backend(path.resolve('.venv/Scripts/python.exe'), ['-u', path.resolve('src/backend.py')], {
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', TANKLOONG_DATA_DIR: temporary, WIN_PD_OVERRIDE_LOCAL_APPDATA: temporary }
  });
  try {
    const health = await backend.request('health');
    assert.equal(health.musicdl, true, health.message);
    assert.equal(health.ffmpeg, true, health.message);
    await assert.rejects(backend.request('search', { keyword: 'test' }), /先选择/);
    const destination = path.join(temporary, '中文 下载目录');
    assert.equal((await backend.request('set_directory', { directory: destination })).directory, destination);
    await assert.rejects(backend.request('download', { resultId: 'not-a-song' }), /无法下载/);
    await assert.rejects(backend.request('unknown'), /未知/);
  } finally { backend.stop(); }
});
test('pending requests reject if subprocess dies', async () => {
  const backend = new Backend(process.execPath, ['-e', 'setInterval(()=>{},1000)']);
  const pending = backend.request('health');
  backend.child.kill();
  await assert.rejects(pending, /停止/);
  await assert.rejects(backend.request('health'), /不可用/);
});
