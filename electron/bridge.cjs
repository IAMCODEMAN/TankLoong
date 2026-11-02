const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { EventEmitter } = require('node:events');

class Backend extends EventEmitter {
  constructor(command, args, options = {}) {
    super();
    this.pending = new Map();
    this.sequence = 0;
    this.child = spawn(command, args, { ...options, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.lastError = '';
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', chunk => { this.lastError = (this.lastError + chunk).slice(-4000); });
    this.child.stdin.on('error', error => this.fail(error));
    createInterface({ input: this.child.stdout }).on('line', line => {
      let message;
      try { message = JSON.parse(line); } catch { return; }
      if (message.event) { this.emit('event', message); return; }
      const item = this.pending.get(message.id);
      if (!item) return;
      clearTimeout(item.timer);
      this.pending.delete(message.id);
      if (message.error) item.reject(new Error(message.error));
      else item.resolve(message.result);
    });
    this.child.on('error', error => this.fail(error));
    this.child.on('exit', code => this.fail(new Error(`音乐服务已停止（${code ?? '退出'}），请重新启动应用。`)));
  }

  request(method, params = {}, timeout = 60000) {
    if (this.dead) return Promise.reject(new Error('音乐服务不可用，请重新启动应用。'));
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('音乐服务响应超时，请稍后重试。'));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }

  fail(error) {
    if (this.dead) return;
    this.dead = true;
    for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(error); }
    this.pending.clear();
    this.emit('event', { event: 'backend-error', message: error.message });
  }

  stop() {
    this.child.stdin.end();
    this.child.kill();
  }
}
module.exports = { Backend };
