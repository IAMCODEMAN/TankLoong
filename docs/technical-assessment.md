# TankLoong 3 源码核对与首版改造记录

核对日期：2026-11-02。项目根目录没有 Git 仓库；重构前将旧 UI 源码、UI 测试、README 和打包定义备份至工作区外 `C:/Users/ROG0/Desktop/TankSnake-legacy-ui-backup-20261102/`。

## 爬虫结论

项目确实使用 CharlesPikachu/musicdl，并非自行仿写爬虫。`requirements.txt` 固定 2.13.6，安装元数据 Home-page 指向该 GitHub 仓库。`src/core.py` 的 `MusicDLAdapter` 直接导入 `MusicClient`，把关键字交给 `search()`，把原始 `SongInfo` 交给 `download()`。各平台的请求、解析和下载规则来自依赖包。

但它不等于 GitHub 最新版。核对时 GitHub HEAD 的版本为 2.14.2，而项目安装的是 2.13.6。对 GitHub tree `dca2f0967e46a8faf8d279737535471102d74ca3` 做递归核对，将本地 Python 文件换行统一为 LF 后计算 Git blob SHA-1：本地 98 个 Python 文件中，30 个与 HEAD 相同、61 个不同、7 个在 HEAD 路径中不存在。这个统计不包含 HEAD 新增但本地不存在的文件，因此不能解释为完整双向差异统计。

入口 `musicdl/musicdl.py` 一致（GitHub blob `9e1a2be3c7bd487d2e3e4a3c298b4684f7a04268`）；默认五个平台的实现文件 QQ、Kuwo、Migu、Netease、Qianqian 均与 HEAD 不同。详细列表保存在 `musicdl-comparison.json`。因此准确说法是“直接使用官方 musicdl 2.13.6”，而非“与 GitHub 最新爬虫完全相同”。首版保留锁定版本，没有擅自升级依赖或修改 site-packages 源码。

应用层与 CLI 的区别：来源隔离和错误诊断、UI 结果映射、并行分源返回、35 秒显示截止、单曲下载文件验证，以及额外的 MP3 编码。本次在适配层配置单次请求超时 `(5, 12)` 秒、重试次数 1；未改变上游平台解析规则。显示截止不能立即终止已进入上游线程中的请求；迟到结果会被丢弃。

## UI 参考

通过 GitHub API 核实用户提供的四个仓库均标记 MIT。实际复用仅来自 kevinbism/liquid-glass-effect 的 README：透明背景、backdrop blur/saturate、内外阴影和伪元素边缘高光；按深色蓝紫配色重写。完整许可见 `licenses/liquid-glass-effect-MIT.txt`。Hyalite 等作为视觉参考，没有引入其代码或宣称实现真实光学折射。

## 技术落地

- 旧 PySide6 `src/main.py` 和 `tests/test_ui.py` 已删除，PySide6 已从运行依赖移除；所有页面在 `ui/` 重新实现。
- Electron 主进程通过 stdin/stdout JSON Lines 启动 Python 服务；musicdl/Rich 输出转入 stderr，避免污染协议。
- 使用主进程原生目录选择器；服务端复验目录可写和磁盘空间；没有自动回退保存到其他位置。
- musicdl 导入阶段默认日志文件发生 PermissionError 的基线问题，通过为子进程配置独立 platformdirs 路径解决。
- FFmpeg 原有 `poll()` 后才读取 stderr 的实现会在真实来源产生大量诊断时堵塞。改为循环 `communicate(timeout=0.2)`，持续排空管道，保留取消检查。增加大 stderr 回归测试。
- 搜索事件可能晚于 IPC 应答到达。搜索增加关联 ID、完成响应携带结果快照，渲染层按 ID 接收并去重，避免结果丢失或旧搜索混入。
- 打包后的 Python 可能忽略 `PYTHONIOENCODING`，已在服务启动时显式将三个标准流固定为 UTF-8，防止中文歌名乱码。FFmpeg 使用 `-nostdin` 与独立空 stdin，避免抢占 JSON 通信管道。
- 下载事件携带每次尝试的关联 ID；迟到的旧事件不能覆盖新重试，也不能将已完成状态退回转码中。

## 验证记录

基线：旧核心 22 项通过、1 项因 musicdl 默认日志目录无权限失败。

新核心和后端覆盖目录先选、分源失败隔离、中文路径、真实 FFmpeg 编码、失败状态清理、同名不覆盖及 stderr 堵塞回归。通信测试覆盖真实 Python 健康检查、目录验证、未知请求以及进程退出后待处理请求失败。

Electron 自动化启动真实窗口，检查目录选择前禁用搜索、结果渲染、不可下载歌曲禁用、HTML 字符串按文本显示、失败与重试成功，以及最小宽度无页面横向溢出。UI 场景中的歌曲为明确标记的测试数据；初始截图使用真实后端健康检查。

真实联网检查：关键字“晴天”，五个来源总计返回 16 条（酷我 5、咪咕 5、网易云 4、千千 2、QQ 0）。选择酷我返回的歌曲实际下载得到 FLAC。首次整条链路暴露 stderr 管道堵塞；修复后对这份真实下载文件再次转码，2.84 秒得到 5,956,974 字节 MP3，并用 FFmpeg 完整解码校验通过（退出码 0）。实时平台可用性会变化，此结果不保证每个平台或每首歌曲均可下载。

自动测试与最终发布包验证结果见工作区根目录 `agent.md` 的最终记录。

最终验证：29 项 Python 测试、2 项 Node 通信测试全部通过；源码版和发布包版 Electron UI 自动化均通过。最终发布包真实搜索“晴天”返回 16 条，完成实际下载、MP3 转码，并对 5,956,974 字节的产物完整解码成功。真实窗口截图为 `screenshots/tankloong-electron-live.png`。发布入口：`release/win-unpacked/TankLoong.exe`。

## 3.0.1 启动故障修复

用户随后反馈：双击发布包出现 `ERR_FAILED (-2) loading file:///.../index.html`。此前带 `--no-sandbox` 的自动化验证不能证明普通桌面启动通过，因此不能将上一节测试结果解读为正常启动可靠性已经得到验证。

确认 `ui/index.html`、`styles.css` 和 `app.js` 均已进入 `resources/app.asar`，主进程可读；不能归因为 HTML 没有打包。3.0.1 将本地 `file://` 加载改为标准、安全的 `tankloong://app/` 内部协议，由主进程从 `app.getAppPath()` 读取静态资源。协议只暴露三个固定资源，拒绝其他主机、任意路径和非 GET/HEAD 请求；继续保留 sandbox、contextIsolation、nodeIntegration=false 和原 CSP。没有使用禁用沙箱作为产品修复。

新增启动前资源校验、成功/失败及渲染器退出日志 `userData/startup.log`；新增归档资源检查并接入打包命令。版本更新为 3.0.1。

本轮验证：5 项 Node 测试通过（含真实 Python 通信及三个资源协议回归）；打包完整性检查通过；发布包带显式 `--no-sandbox` 的界面功能回归通过。默认沙箱的直接 EXE 启动在当前 Codex 执行环境中于应用日志产生前退出，代码为 `0x80000003`，禁用 GPU 亦然；Windows Shell 启动也未观察到成功日志。因此本轮尚不能声称普通用户双击启动已实机验证。未新增联网下载测试，核心爬虫和转码代码未改动。

## 2026-11-04 后续结论与首发验证

进一步实验定位到生成目录继承的 Windows Low Mandatory Level：同一份 Electron 和发布包复制到标准目录，或仅将发布目录完整性级别恢复为 Medium 后，默认沙箱启动恢复。新增 tools/prepare-release.cjs 仅处理固定发布目录，并接入 pack。首发前再次通过 5 项 Node 测试、发布包资源检查及无调试器默认沙箱原生 EXE 启动检查。上述早期启动验证限制已解除；本次未重新联网下载歌曲。
