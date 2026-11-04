const asar = require('@electron/asar');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const release = path.resolve('release/win-unpacked');
const archive = path.join(release, 'resources/app.asar');
for (const entry of ['package.json', 'electron/main.cjs', 'electron/preload.cjs', 'electron/assets.cjs', 'electron/bridge.cjs', 'ui/index.html', 'ui/styles.css', 'ui/app.js']) {
  assert.ok(asar.extractFile(archive, entry).length, `Missing packaged resource: ${entry}`);
}
assert.equal(JSON.parse(asar.extractFile(archive, 'package.json')).version, require('../package.json').version);
for (const entry of ['TankLoong.exe', 'resources/backend/tankloong-backend.exe', 'resources/backend/_internal/ffmpeg/bin/ffmpeg.exe']) {
  assert.ok(fs.statSync(path.join(release, entry)).size > 0, `Missing release runtime: ${entry}`);
}
console.log('Release resources verified: UI, preload, protocol, Python service and FFmpeg.');
