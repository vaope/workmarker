# WorkEventAgent 客户端（Electron）

WorkEventAgent 的桌面客户端，在已验证的 Python 归档核心外包一层桌面壳。

- 归档核心：`../workeventagent/`（Python，356 tests + 26 subtests 全绿）
- LLM 入口：opencode（唯一）
- 真相源：项目 Markdown；索引：SQLite；附件：`<workspace>/attachments/`

## 运行

```bash
cd client
npm.cmd start
```

也可以直接双击或运行：

```bat
client\start-client.cmd
```

说明：在某些 Windows PowerShell 环境里，`npm start` 会优先命中 `npm.ps1`，并被系统执行策略拦住。`npm.cmd start` 和 `start-client.cmd` 不走 PowerShell 脚本策略。

首次启动会引导选择「项目库目录」（workspace）。之后所有项目文档 / 附件 / 索引都存这里。

## 依赖

- Node.js + npm（已验证 node v22）
- Python 3.11+（已验证 3.13，须在 PATH —— 客户端通过 `python -m workeventagent.gui <cmd>` 调 backend）
- opencode CLI（在 PATH，归档时调用，约 10-30s/次）

安装版会把 `workeventagent/` Python 包一并放入 Electron resources，但仍要求系统 PATH 中有 Python 3.11+ 和 opencode CLI。

## Windows 安装包与自动更新

生成本地 NSIS 安装包（不发布）：

```bash
cd client
npm.cmd run test:update
npm.cmd run dist:win
```

如果本机连接 GitHub 的 electron-builder 工具包下载超时，可在 PowerShell 临时切换镜像；下载内容仍会按 electron-builder 内置校验和验证：

```powershell
$env:ELECTRON_BUILDER_BINARIES_MIRROR='https://npmmirror.com/mirrors/electron-builder-binaries/'
npm.cmd run dist:win
```

产物在 `client/dist/`，包括安装程序、blockmap 和 `latest.yml`。安装版启动 5 秒后自动检查 `vaope/workmarker` 的 GitHub Releases；也可以在「设置 → 应用更新」手动检查。发现更新后，下载和重启安装都需要用户点击确认。

发布新版本前先更新 `client/package.json` 的 `version`，再设置具备仓库写权限的 `GH_TOKEN`：

```bash
cd client
npm.cmd run release:win
```

Phase 1 的 Windows 安装包尚未做代码签名，Windows SmartScreen 可能显示警告。开发态 `npm start` 不访问更新源。

## 重装 electron 的坑（重要）

本机 `NODE_ENV=production` + `npm config omit=dev`，会导致 `npm install` **静默跳过 devDependencies（electron）**，装完只有 1 个包、`dist/electron.exe` 缺失。若 `node_modules` 丢失需重装：

```bash
cd client
NODE_ENV=development ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ npm install --include=dev
# 若二进制仍缺（postinstall 未下载），手动触发：
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ node node_modules/electron/install.js
# 验证：
ls node_modules/electron/dist/electron.exe   # 应为 ~188MB
```

## 功能

- **工作地图（主界面）**：项目 → 工作项 → 任务三层结构，任务 checkbox 直接勾选完成
- **今日摘要（侧栏）**：待确认捕获数 + 当前项目待推进任务数，一键跳转
- **收件箱优先捕获**：主窗口和快速捕获都先进收件箱，后台 opencode 解析，不阻塞连续输入
- **项目库左栏**：项目列表及未完成任务数
- **报告 / 搜索 / 收件箱**：独立 tab 切换
- **快速捕获浮窗**：全局热键 `Ctrl+Shift+Space` 唤起
- **报告生成**：日报/周报/自定义日期范围/项目总结，可持久化到 `<workspace>/reports/`
- **定时报告**：日报和周报可按设定的时间自动生成
- **粘贴图片**：`Ctrl+V` 粘贴剪贴板图片，归档为附件
- **项目初始化表单**：左下角「+ 新建项目」

> 说明：checkbox 勾选只改任务当前状态，**不写入时间线，也不会出现在日报/周报中**。要进报表必须走捕获（capture）。时间线数据仍保留给搜索、纠错和审计。

## 项目文档 v2（F007 Phase A）

项目文档已升级到 v2 格式（`schema_version: 2`），新增：

- **项目全景（主视图默认）**：项目档案 → 当前全景 → 工作地图 → 技术概览 → 关键认知 → 关键决策 → 附件 → 事件证据 → 历史摘要，9 个区块各有所有权（需审阅 / 派生 / 结构化 / 只追加）
- **hash 守门编辑**：需审阅区块（项目档案、技术概览、关键认知）通过模态编辑，发送 base hash，stale 时拒绝写入
- **v1→v2 迁移**：旧项目可预览 diff → 确认迁移，自动写 `.workeventagent/backups/<project_id>/<ts>.md` 备份，原子替换，读回校验
- **不变量**：Timeline 仍是报告/搜索/纠错/索引重建的证据源；Work Map 保持 F004 交互密度；新项目默认 v2，旧项目不强制迁移

## 项目知识综合（F007 Phase B）

- **可信触发**：普通捕获只归档事实；高影响捕获在事实写入前持久化任务，归档成功后再进入综合队列；每日、每周和定向综合都使用可恢复的持久任务。
- **统一审核入口**：收件箱同时展示捕获卡与独立持久化的知识提案，包含来源事件、影响维度、before/after 与 diff。
- **整包确认**：多区块提案确认后全有或全无地原子写入；任一来源事件或 section hash 过期都会拒绝整包，不自动 rebase。
- **可选模块文档**：由 wrapper 生成稳定 ID、文件名与顺序，必须单独确认，且主文档保留摘要后才能创建。
- **周期综合**：设置中可配置每日/每周综合；schedule run 以完整项目清单为边界，失败 child 重试成功后才推进周期成功标记。
- **无隐式写入**：浏览、搜索、生成提案和状态恢复都不会自动应用项目知识；只有用户确认会写入。

## Reports

The Reports tab can generate and save Markdown reports under `<workspace>/reports/`.

Scheduled reports run while the Electron app or tray process is alive:
- Daily reports use the computer-local date and skip days with no Timeline events.
- Weekly reports use the selected local weekday and skip weeks with no Timeline events.
- Opening the app does not generate missed reports automatically.

Manual reports support explicit `date_from` and `date_to` values.

## 架构与接口契约

见 `../docs/designs/F001-client-architecture.md`（Python backend 6 命令 JSON 契约 + Electron 结构 + IPC）。

## 已验证

- 356 Python tests + 26 subtests 全绿（含 Python bridge 模块入口、知识账本、崩溃恢复、周期 manifest、整包原子应用、可选模块治理、Unicode 行分隔符守门与 renderer）
- 真实 opencode 端到端归档闭环（init → propose → commit → timeline，Markdown + SQLite 真实写入）
- opencode 1.18.1 的 `workevent-synthesizer` 真实契约 smoke：只读输入、无变化时返回有界 JSON，agent 不拥有项目/提案/来源/hash 身份字段
- Electron v33 在隔离 workspace 和 1040×700 窗口下完成 Phase B 12 项程序化验收：事件选择、统一审核、证据/diff、HTML 转义、整包确认、影响 badge、滚动/溢出、搜索定向入口、周期设置和无自动应用全部通过
- 11 个客户端 JavaScript 文件全部通过 `node --check`

## 仍需人工体验验证

程序化 Electron 验收已经覆盖核心布局和交互可达性；仍需人工体验鼠标/触控板手感、粘贴缩略图、浮窗定位、真实全局热键冲突与多显示器行为。
