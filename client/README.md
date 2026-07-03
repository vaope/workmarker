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

- **双栏主窗口**：左侧项目库（项目名/未完成任务数/更新时间），右侧任务视图 + 时间线视图 + 报告视图切换，底部常驻输入条
- **快速捕获浮窗**：全局热键 `Ctrl+Shift+Space` 唤起，独立于主窗口；热键可在左下角设置中修改
- **报告生成**：支持日报/周报/自定义日期范围/项目总结，报告可持久化到 `<workspace>/reports/`
- **定时报告**：日报和周报可按设定的时间自动生成（仅应用/托盘运行时）
- **粘贴图片**：`Ctrl+V` 粘贴剪贴板图片，归档为附件（图片不送 LLM，仅按路径归档）
- **确认卡片**：确认 / 编辑 / 取消；低置信度（<70%）下拉修正，不绕过确认
- **项目初始化表单**：左下角「+ 新建项目」

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

- 121 Python tests 全绿（含 backend 命令 + registry + timeline 解析 + 报告生成 + 本地时间过滤）
- 真实 opencode 端到端归档闭环（init → propose → commit → timeline，Markdown + SQLite 真实写入）
- electron v33 启动 smoke test 无崩溃（主进程加载窗口 + 托盘 + 全局热键）

## 待真机验证（无头环境无法覆盖）

GUI 视觉渲染与交互（双栏布局、确认卡片、粘贴缩略图、浮窗定位、热键实际唤起）需在有显示器的环境实跑确认。
