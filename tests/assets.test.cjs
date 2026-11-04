const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createAssetHandler, validateAssets } = require('../electron/assets.cjs');

test('app protocol serves complete HTML/CSS/JS with their correct MIME types', async () => {
  const directory = path.resolve('ui');
  await validateAssets(directory);
  const serve = createAssetHandler(directory);
  for (const [file, mime] of [['index.html', 'text/html'], ['styles.css', 'text/css'], ['app.js', 'application/javascript']]) {
    const response = await serve(new Request(`tankloong://app/${file}`));
    assert.equal(response.status, 200);
    assert.ok(response.headers.get('content-type').startsWith(mime));
    assert.equal(await response.text(), await fs.readFile(path.join(directory, file), 'utf8'));
  }
});

test('app protocol refuses other hosts, methods and arbitrary filesystem paths', async () => {
  const serve = createAssetHandler(path.resolve('ui'));
  for (const url of ['tankloong://other/index.html', 'tankloong://app/electron/main.cjs', 'tankloong://app/%2e%2e%2felectron/main.cjs', 'file:///index.html']) {
    assert.equal((await serve(new Request(url))).status, 404);
  }
  assert.equal((await serve(new Request('tankloong://app/index.html', { method: 'POST' }))).status, 405);
});

test('missing release resources fail validation before a hidden window is created', async () => {
  await assert.rejects(validateAssets(path.resolve('artifacts/missing-ui')), /ENOENT/);
  const errors = [];
  const response = await createAssetHandler(path.resolve('artifacts/missing-ui'), error => errors.push(error))(new Request('tankloong://app/index.html'));
  assert.equal(response.status, 500);
  assert.equal(errors.length, 1);
});
