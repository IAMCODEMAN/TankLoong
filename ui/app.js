/* Renderer has no Node.js access. All filesystem work goes through preload. */
const $ = selector => document.querySelector(selector);
const api = window.tankloong;
const state = { directory: '', ready: false, searching: false, choosing: false, tracks: new Map(), downloads: new Map(), completedSources: 0 };
const sourceNames = { Migu: '咪咕', Netease: '网易云', QQ: 'QQ 音乐', Kuwo: '酷我', Qianqian: '千千' };
const statusNames = { queued: '等待下载', downloading: '下载中', converting: '转为 MP3', done: '已完成', failed: '下载失败' };
function node(tag, className, text) { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; }
function icon(name) { const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', `#i-${name}`); svg.append(use); return svg; }
function notice(message = '') { $('#notice').textContent = message; $('#notice').hidden = !message; }
function updateControls() {
  const active = [...state.downloads.values()].some(job => !['done', 'failed'].includes(job.state));
  $('#choose-directory').disabled = !state.ready || state.choosing || state.searching || active;
  $('#keyword').disabled = !state.ready || !state.directory || state.searching;
  $('#search-button').disabled = !state.ready || !state.directory || state.searching;
  $('#search-button span').textContent = state.searching ? '正在搜索' : '搜索音乐';
  $('#open-directory').disabled = !state.directory;
}
function setEmpty(title, description, searching = false) {
  const empty = $('#search-empty');
  empty.hidden = false;
  empty.classList.toggle('searching', searching);
  empty.querySelector('h3').textContent = title;
  empty.querySelector('p').textContent = description;
}
function renderTracks(items) {
  if (items.length) $('#search-empty').hidden = true;
  for (const track of items) {
    if (state.tracks.has(track.id)) continue;
    state.tracks.set(track.id, track);
    const row = node('div', 'track'); row.setAttribute('role', 'listitem');
    row.append(node('span', 'track-number', String(state.tracks.size).padStart(2, '0')));
    const art = node('span', 'track-art'); art.append(icon('note')); row.append(art);
    const info = node('div', 'track-info'); const title = node('div', 'track-title', track.title); title.title = track.title;
    info.append(title, node('div', 'track-subtitle', `${track.artist} · ${track.album}`)); row.append(info);
    row.append(node('span', 'source-label', sourceNames[track.source] || track.source), node('span', 'track-duration', track.duration));
    const button = node('button', 'track-download'); button.append(icon('download')); button.disabled = !track.downloadable;
    button.title = track.downloadable ? '下载并转换为 MP3' : '该来源暂无可用音频'; button.setAttribute('aria-label', `下载 ${track.title}`);
    button.addEventListener('click', () => startDownload(track)); row.append(button); $('#track-list').append(row);
    track.button = button;
  }
  $('#result-count').textContent = `${state.tracks.size} 首`;
}
function renderDownloads() {
  const list = $('#download-list'); list.replaceChildren();
  $('#download-count').textContent = state.downloads.size;
  for (const job of [...state.downloads.values()].reverse()) {
    const item = node('div', `download-item ${job.state}`);
    const top = node('div', 'download-item-top');
    const title = node('span', 'download-title', job.title); title.title = job.title;
    top.append(title, node('span', 'download-state', statusNames[job.state])); item.append(top);
    item.append(node('div', 'download-detail', job.message || (job.state === 'done' ? job.outputPath : `${job.artist} · ${job.state === 'converting' ? 'FFmpeg 正在编码，请稍候' : 'MP3 · 保存到所选目录'}`)));
    item.append(node('div', 'busy-line'));
    if (job.state === 'done' || job.state === 'failed') {
      const action = node('button', 'job-action', job.state === 'done' ? '在文件夹中显示 ↗' : '重试下载 ↻');
      action.addEventListener('click', () => job.state === 'done' ? api.reveal(job.outputPath).catch(error => notice(error.message)) : startDownload(job.track));
      item.append(action);
    }
    list.append(item);
  }
  $('#step-three').classList.toggle('active', state.downloads.size > 0);
  updateControls();
}
async function startDownload(track) {
  const existing = state.downloads.get(track.id);
  if (existing && !['failed', 'done'].includes(existing.state)) return;
  const job = { track, title: track.title, artist: track.artist, state: 'queued', downloadId: crypto.randomUUID() };
  state.downloads.set(track.id, job); track.button.disabled = true; renderDownloads(); notice();
  try {
    const result = await api.download(track.id, job.downloadId);
    Object.assign(job, { state: 'done', ...result }); track.button.replaceChildren(icon('check'));
  } catch (error) { job.state = 'failed'; job.message = error.message; track.button.disabled = false; }
  renderDownloads();
}
api.onEvent(event => {
  if (event.event === 'backend-error') {
    state.ready = false; updateControls(); notice(event.message);
    $('#connection-text').textContent = '音乐服务不可用'; $('#connection-dot').className = 'error';
    for (const job of state.downloads.values()) {
      if (!['done', 'failed'].includes(job.state)) { job.state = 'failed'; job.message = event.message; }
    }
    if (state.downloads.size) renderDownloads();
  }
  if (event.event === 'search-source' && event.searchId === state.searchId) {
    state.completedSources++;
    renderTracks(event.results);
    if (state.searching) $('#source-status').textContent = `正在搜索 · ${state.completedSources}/5 个来源已返回`;
  }
  if (event.event === 'download') {
    const job = state.downloads.get(event.resultId);
    if (!job || event.downloadId !== job.downloadId) return;
    if (['done', 'failed'].includes(job.state) && !['done', 'failed'].includes(event.state)) return;
    Object.assign(job, { state: event.state, outputPath: event.outputPath, message: event.message }); renderDownloads();
  }
});
$('#choose-directory').addEventListener('click', async () => {
  state.choosing = true; updateControls();
  try {
    const directory = await api.chooseDirectory(); if (!directory) return;
    state.directory = directory; $('#directory-path').textContent = directory; $('#directory-path').title = directory;
    $('#directory-badge').textContent = '已就绪'; $('#choose-directory').firstChild.textContent = '更换位置';
    $('#step-two').classList.add('active'); $('#search-hint').textContent = '支持歌名、歌手，或组合关键词搜索'; notice();
  } catch (error) { notice(error.message); }
  finally { state.choosing = false; updateControls(); if (state.directory) $('#keyword').focus(); }
});
$('#search-form').addEventListener('submit', async event => {
  event.preventDefault(); const keyword = $('#keyword').value.trim();
  if (!keyword || !state.directory || state.searching || !state.ready) return;
  state.searching = true; state.searchId = crypto.randomUUID(); state.completedSources = 0; state.tracks.clear(); $('#track-list').replaceChildren();
  $('#result-count').textContent = '搜索中'; $('#source-status').textContent = '正在连接 5 个音乐来源';
  setEmpty('正在寻找你的旋律', '各来源将陆续返回结果，最长等待约 35 秒。', true); notice(); updateControls();
  try {
    const result = await api.search(keyword, state.searchId);
    renderTracks(result.results || []);
    const unavailable = result.diagnostics.filter(item => ['error', 'timeout'].includes(item.status)).length;
    $('#source-status').textContent = unavailable ? `${unavailable} 个来源暂不可用` : '搜索完成';
    $('#result-count').textContent = `${state.tracks.size} 首`;
    if (!state.tracks.size) setEmpty('暂时没有找到这首歌', '试试更简短的歌名，或加上歌手名称。');
    if (unavailable) notice('部分音乐来源请求失败或超时；你可以下载已返回的歌曲，或稍后重试。');
  } catch (error) { notice(error.message); $('#source-status').textContent = '搜索失败'; if (!state.tracks.size) setEmpty('这次搜索未能完成', '请检查网络连接，然后重试。'); }
  finally { state.searching = false; updateControls(); }
});
$('#open-directory').addEventListener('click', () => api.openDirectory().then(error => { if (error) notice(error); }).catch(error => notice(error.message)));
document.querySelectorAll('[data-window]').forEach(button => button.addEventListener('click', () => api.windowControl(button.dataset.window)));
updateControls();
api.initial().then(info => {
  state.ready = info.musicdl && info.ffmpeg;
  $('#connection-dot').className = state.ready ? 'ready' : 'error';
  $('#connection-text').textContent = state.ready ? '音乐服务已就绪' : '音乐服务需要检查';
  $('#app-version').textContent = info.version;
  if (info.savedDirectory) { $('#directory-path').textContent = `上次使用：${info.savedDirectory}`; $('#directory-path').title = info.savedDirectory; }
  if (info.message) notice(info.message); updateControls();
}).catch(error => { notice(error.message); $('#connection-text').textContent = '音乐服务连接失败'; $('#connection-dot').className = 'error'; });
