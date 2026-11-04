// Chromium cannot start its sandbox from binaries labelled Low integrity.
// A build workspace can inherit that label from an automation environment.
// Normalize only our generated Windows release, never the user's workspace.
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

if (process.platform === 'win32') {
  const release = path.resolve(__dirname, '../release/win-unpacked');
  if (!fs.statSync(release).isDirectory() || fs.lstatSync(release).isSymbolicLink()) {
    throw new Error(`Expected generated release directory: ${release}`);
  }
  execFileSync('icacls.exe', [release, '/setintegritylevel', '(OI)(CI)M', '/T', '/Q'], {
    windowsHide: true, stdio: 'inherit'
  });
}
