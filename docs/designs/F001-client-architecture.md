---
feature_ids: [F001]
topics: [client, architecture, electron, ipc, backend-contract]
doc_kind: design
created: 2026-07-01
---

# F001 WorkEventAgent 客户端架构与接口契约

> 本文件是客户端实现的单一真相源。Python backend 由金哥实现，Electron 前端由砚砚实现，双方照此契约对齐。
> 真相源优先级：本文件定义接口边界；`docs/designs/F001-client-ux-spec.md` 定义交互细节；`docs/WORKLOG_SCHEMA.md` 定义文档写入协议。

## 1. 技术栈决策：Tauri → Electron

**决策**：客户端壳从 Tauri v2 改为 **Electron**。

**理由（第一性原理）**：
- 本机环境实测 `cargo` / `rustc` 均 **NOT FOUND**，Tauri v2 依赖 Rust 工具链 + MSVC build tools + 首次编译，今晚不可行。
- node v22 / npm / pnpm / python 3.13 / opencode 1.17 均就绪。Electron 走 node 栈，零新增工具链，预编译二进制，`npm install` 即用。
- 客户端核心需求（全局热键、系统托盘、剪贴板图片、独立浮窗、多窗口、文件系统）Electron 全部一等公民内置支持，零额外依赖。
- co-creator 明确「好用 > 好看」；UX spec 是 web 风格设计（卡片/双栏/toast/缩略图），HTML/CSS 渲染天然适配。
- 宪宪在传球 Tradeoff 中已预授权：「若 Rust/Tauri 环境不可用，今晚优先保证可直接运行客户端，可用本地可运行桌面壳先闭环」。

**不变量**：归档核心继续复用已验证的 Python 模块（31 tests 全绿），opencode 仍是唯一 LLM 执行入口。客户端只在外面包一层壳，不重写核心。

## 2. 系统架构

```text
Electron 客户端 (client/)
  main process (Node)
    ├─ 窗口管理：主窗口 BrowserWindow + 快速捕获浮窗 BrowserWindow
    ├─ globalShortcut：全局热键唤起捕获窗（默认 Ctrl+Shift+Space）
    ├─ Tray：系统托盘 + 菜单
    ├─ clipboard.readImage：剪贴板图片 → 暂存 temp
    ├─ ipcMain：处理渲染层请求
    └─ python_bridge：spawn `python -m workeventagent.gui <cmd>`，stdin/stdout JSON
         │
         ▼
  Python GUI backend (workeventagent/gui.py + registry.py)
    propose / commit / projects / tasks / timeline / init
    复用 → opencode_runner / markdown_store / index_store / ids / models
         │
         ▼
  数据层：项目 Markdown（真相源） + 全局 index.sqlite（索引） + attachments/
```

## 3. Python GUI Backend 契约

**入口**：`python -m workeventagent.gui <command>`
**通信**：请求 JSON 从 **stdin** 读（UTF-8）；响应 JSON 写 **stdout**（UTF-8，单行或紧凑）。
**约定**：
- 所有响应顶层含 `ok: bool`。
- 业务失败 → `{"ok": false, "kind": "<错误类型>", "error": "<人类可读>"}`，进程 **exit 0**（让前端区分「业务失败」与「进程崩溃」）。
- 仅未捕获异常 / 无法产出 JSON 时 exit 非 0。
- backend **不得调用 input()**，无任何交互。
- stdout 只输出最终 JSON；调试信息走 stderr。

### 3.1 propose — 生成归档提案（不写文件）

请求：
```json
{"text": "今天看了KV cache阻塞点...", "project_path": "D:/worklogs/multimodal-labeling.md", "attachments": ["C:/Temp/wea/pending/a.png"]}
```
处理：read project → `run_archivist` → 收集现有 event_ids → `make_event_id` → `parse_archivist_output` → new_task 防碰撞（`make_unique_stable_id`）→ 填充 attachment_paths。
响应成功：
```json
{"ok": true,
 "proposal": {
   "target": {"project_id":"...","item_id":"...","task_id":"...","task_title":"","new_item":false,"new_task":false},
   "confidence": 0.92,
   "reason": "...",
   "event": {"event_id":"20260701-...","task_id":"...","input_text":"...","summary":"...","status":"in_progress","next_action":"..."},
   "attachment_paths": ["a.png"]
 },
 "low_confidence": false}
```
- `low_confidence = confidence < 0.7`（UX spec：<70% 走下拉修正，不直接拒绝；阈值判定交给前端，backend 只给标记 + 原始 confidence）。
- `event_id` 在此阶段生成并固定，commit 时原样使用。
响应失败：`kind ∈ {"opencode_error","parse_error"}`。

### 3.2 commit — 确认后写入

请求：
```json
{"proposal": {...同 propose 的 proposal，可能被前端编辑过...},
 "project_path": "...", "db_path": "D:/worklogs/index.sqlite",
 "pending_attachments": [{"temp_path":"C:/Temp/wea/pending/a.png","filename":"a.png"}]}
```
处理顺序（严格）：
1. 复制每个 pending_attachment：`temp_path` → `<project_dir>/attachments/<task_id>/<event_ts>-<index><ext>`，得到相对项目目录的 posix 路径。
2. 用复制后的相对路径覆盖 `proposal.attachment_paths`。
3. `new_task` → `insert_new_task` 后 `apply_proposal`；否则直接 `apply_proposal`。
4. `append_attachments`。
5. `write_project_atomically`（先 Markdown）。
6. `init_db` + `rebuild_index`（后 SQLite）。
响应：`{"ok":true,"written_path":"...","archived_attachments":["attachments/kv-cache-blockers/20260701-...-0.png"],"task_id":"..."}`

### 3.3 projects — 项目库列表

请求：`{"workspace":"D:/worklogs","db_path":"D:/worklogs/index.sqlite"}`
处理：扫描 workspace 下 `*.md`，取 frontmatter `doc_kind: work_project`；统计 `status: in_progress` 任务数；读 `updated`。
响应：`{"ok":true,"projects":[{"project_id":"...","title":"...","path":"...","open_task_count":3,"updated_at":"2026-07-01"}]}`

### 3.4 tasks — 单项目任务树

请求：`{"project_path":"..."}`
处理：解析 Work Map，按 item 分组；每个 task 含 status/next_action/last_event_id/title；`updated_at` 取该 task 最近 timeline 事件时间（无则空）。
响应：
```json
{"ok":true,"project_id":"...","title":"...",
 "items":[{"item_id":"...","title":"...",
   "tasks":[{"task_id":"...","title":"...","status":"in_progress","next_action":"...","last_event_id":"...","updated_at":"..."}]}]}
```

### 3.5 timeline — 全项目时间线

请求：`{"project_path":"..."}`
处理：解析 `## Timeline` 段全部事件，**时间倒序**；关联 item/task 标题；判断该事件是否有附件。
响应：
```json
{"ok":true,"events":[{"timestamp":"2026-07-01T...","event_id":"...","task_id":"...","item_id":"...","task_title":"...","summary":"...","status":"in_progress","next_action":"...","input":"...","has_attachment":false}]}
```
> 注：`## Timeline` 段解析器是新增能力（现有 index_store 只解析 Work Map + Attachments，不解析 Timeline 历史）。

### 3.6 init — 初始化新项目

请求：
```json
{"workspace":"D:/worklogs","title":"基于大模型的多模态标注系统","project_id":"multimodal-labeling",
 "items":[{"title":"使用 KV cache 优化 few-shot","tasks":["查看当前阻塞点","KV cache 原理解读"]}],
 "db_path":"D:/worklogs/index.sqlite"}
```
处理：按 WORKLOG_SCHEMA 生成标准 Markdown（frontmatter + 6 段：Current Snapshot/Work Map/Decisions/Attachments/Timeline/Daily-Weekly Rollups；item/task 带 anchor，task 初始 `status: in_progress` / `next_action:` / `last_event_id:`）→ 写 `<workspace>/<project_id>.md` → 创建 `<workspace>/attachments/` → init_db + rebuild_index → 回读校验可解析。
- `project_id` 缺省由 `make_stable_id(title)` 生成；前端可覆盖。
- 已存在同名 `project_id` → `{"ok":false,"kind":"exists"}`，不覆盖。
响应：`{"ok":true,"project_path":"...","project_id":"..."}`

## 4. 项目库 Registry（registry.py）

- workspace 根目录是项目库容器：每项目一个 `<project_id>.md`，一个全局 `index.sqlite`，一个共享 `attachments/`。
- 项目发现 = 扫描 workspace 下含 `doc_kind: work_project` 的 `*.md`（文件系统即真相源，无独立注册表文件，避免第二真相源）。
- workspace 根路径由 Electron 侧 config 持久化（见 §6），作为参数传给每个 backend 命令。

## 5. Electron 客户端结构 + IPC

```text
client/
  package.json          # electron 依赖 + start 脚本
  main.js               # 主进程：窗口/热键/托盘/剪贴板/IPC/桥
  preload.js            # contextBridge 暴露 window.wea.*
  python_bridge.js      # spawn python -m workeventagent.gui，JSON 往返
  config.js             # userData/config.json 读写（workspace、热键）
  windows/
    main.html  main.css  main.js        # 主窗口渲染
    capture.html capture.css capture.js # 快速捕获浮窗渲染
  assets/               # 托盘/应用图标
```

preload 暴露（`window.wea`）：
| 方法 | 映射 backend / 行为 |
|---|---|
| `propose(text, projectPath, attachments)` | gui propose |
| `commit(proposal, projectPath, dbPath, pendingAttachments)` | gui commit |
| `listProjects()` | gui projects（workspace/db 由主进程从 config 注入） |
| `listTasks(projectPath)` | gui tasks |
| `listTimeline(projectPath)` | gui timeline |
| `initProject(spec)` | gui init |
| `readClipboardImage()` | 主进程 clipboard.readImage → 写 temp → 返回 {tempPath, filename} 或 null |
| `getConfig()` / `setWorkspace(path)` | config 读写 |
| `pickWorkspaceDir()` | dialog 选目录 |
| `onShowCapture(cb)` | 主进程→渲染：全局热键触发 |
| `hideCapture()` / `resizeInput(h)` | 窗口控制 |

安全：`contextIsolation: true`、`nodeIntegration: false`、仅通过 preload 白名单 IPC；渲染层不直接碰 node/fs。

## 6. 配置与默认值

- config 路径：`app.getPath('userData')/config.json`。
- 字段：`{"workspace": "<abs>", "dbPath": "<workspace>/index.sqlite", "hotkey": "CommandOrControl+Shift+Space", "pythonCmd": "python"}`。
- 首启 workspace 为空 → 引导选目录（默认建议 `Documents/WorkEventAgent`）。
- temp 暂存：`os.tmpdir()/workeventagent/pending/`。

## 7. 附件归档流程（端到端）

```text
渲染层 Ctrl+V → wea.readClipboardImage() → 主进程存 temp → 返回 {tempPath, filename}
渲染层展示缩略图（多张横排，可单删）
提交 → wea.propose(text, projectPath, [tempPaths])（图片不送 LLM，仅记录）
确认卡片展示附件
确认 → wea.commit(proposal, ..., pendingAttachments=[{tempPath,filename}])
  → backend 复制到 attachments/<task_id>/<ts>-<idx><ext> → 写 Markdown → 重建 SQLite
取消 → 渲染层清理 temp，保留文字
```

## 8. 分工与验收标准

| 线 | 负责 | 内容 |
|---|---|---|
| A — Python backend | 金哥 | §3 六命令 + §4 registry + 附件复制 + Timeline 解析器 + unittest |
| B — Electron 前端 | 砚砚 | §5 脚手架/主进程/桥 + 主窗口 + 快速捕获窗口 |
| 集成验收 | 砚砚 | 端到端 + 启动 + 全功能走查 |

验收清单（co-creator「可直接运行」硬目标）：
- [ ] 现有 31 Python tests 不破
- [ ] backend 六命令各有 unittest，真实 opencode propose 产出合法 JSON
- [ ] `npm start` 能启动 Electron 主窗口，无报错
- [ ] 新建项目 → 生成合规 Markdown + attachments 目录 + 入索引
- [ ] 输入一句进展 → 确认卡片 → 确认 → Markdown 追加 Timeline + 更新 Work Map + SQLite 更新
- [ ] 任务视图 / 时间线视图可见且数据正确
- [ ] 全局热键唤起快速捕获窗口，粘贴图片可归档到 attachments/
- [ ] 低置信度（<70%）走下拉修正，不绕过确认
