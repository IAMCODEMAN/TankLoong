const fs = require('node:fs/promises');
const path = require('node:path');

const APP_URL = 'tankloong://app/index.html';
const ASSETS = Object.freeze({
  '/index.html': ['index.html', 'text/html; charset=utf-8'],
  '/styles.css': ['styles.css', 'text/css; charset=utf-8'],
  '/app.js': ['app.js', 'application/javascript; charset=utf-8']
});

async function validateAssets(directory) {
  for (const [filename] of Object.values(ASSETS)) {
    const content = await fs.readFile(path.join(directory, filename));
    if (!content.length) throw new Error(`界面资源为空：${filename}`);
  }
}

function createAssetHandler(directory, onError = () => {}) {
  return async request => {
    const url = new URL(request.url);
    const asset = url.protocol === 'tankloong:' && url.hostname === 'app' &&
      !url.port && !url.username && !url.password && ASSETS[url.pathname];
    if (!asset) return new Response('Not found', { status: 404 });
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    }
    try {
      // Main-process filesystem access also understands ASAR. Renderer sandbox
      // no longer needs to open a Windows file:// path inside the archive.
      const content = await fs.readFile(path.join(directory, asset[0]));
      return new Response(request.method === 'HEAD' ? null : content, { headers: {
        'Content-Type': asset[1], 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'
      } });
    } catch (error) {
      onError(error);
      return new Response('Application resource unavailable', { status: 500 });
    }
  };
}
module.exports = { APP_URL, ASSETS, createAssetHandler, validateAssets };
