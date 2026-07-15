# WorkEventAgent 客户端（Electron）

WorkEventAgent 的桌面客户端，在已验证的 Python 归档核心外包一层桌面壳。

- 归档核心：`../workeventagent/`（Python，121 tests 全绿）
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

- 180 Python tests 全绿（含 backend 命令 + registry + timeline 解析 + 报告生成 + 本地时间过滤 + Work Map renderer + 捕获路径）
- 真实 opencode 端到端归档闭环（init → propose → commit → timeline，Markdown + SQLite 真实写入）
- electron v33 启动 smoke test 无崩溃（主进程加载窗口 + 托盘 + 全局热键）

## 待真机验证（无头环境无法覆盖）

GUI 视觉渲染与交互（双栏布局、确认卡片、粘贴缩略图、浮窗定位、热键实际唤起）需在有显示器的环境实跑确认。
