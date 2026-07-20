---
feature_ids: [F008]
related_features: [F001]
topics: [electron, auto-update, github-releases, windows, packaging]
doc_kind: spec
created: 2026-07-20
---

# F008：桌面客户端自动更新

> Status: review | Owner: @cat-o1e4zfgp | Reviewer: @cat-772lxe06

## Why

WorkEventAgent 目前只能从源码运行。客户端持续演进后，用户不应手动拉代码和重装依赖才能获得修复。Phase 1 建立一个可发布、可验证、由用户控制安装时机的 Windows 更新闭环。

## Phase 1 范围

- 用 `electron-builder` 生成 Windows NSIS 安装包和更新元数据。
- 安装包启动 5 秒后从 `vaope/workmarker` GitHub Releases 检查新版本。
- 设置页展示当前版本、检查状态、发布说明、下载进度和操作按钮。
- 检测到新版本后由用户确认下载；下载完成后由用户确认重启安装。
- Python 包作为 Electron `extraResources` 随安装包分发，运行时仍使用用户系统中的 Python 和 opencode CLI。
- 开发态不访问更新源，并明确显示只有安装包支持更新。

## 非目标

- macOS / Linux 安装包。
- 无确认的自动下载、强制重启或后台安装。
- Python 运行时和 opencode CLI 的内置分发。
- 数据库迁移、上一版本自动回滚、离线更新和增量包大小目标。
- 代码签名；未签名的首版安装包仍可能触发 Windows SmartScreen。

## 边界与数据流

`update_manager.js` 是更新状态机，只依赖注入的 `autoUpdater`，负责把 Electron 事件转换成可序列化状态。主进程通过四个有界 IPC 命令暴露查询、检查、下载和安装，通过一个只读事件通道推送状态。renderer 只渲染文本和进度，不接触文件路径、feed URL 或 updater 对象。

发布源由 `package.json` 的 `build.publish` 固定到 GitHub。`electron-updater` 校验 `latest.yml` 中的 SHA-512；应用不执行 release notes 中的 HTML。更新失败不影响现有版本继续运行。

## 验收标准

- [x] `npm run test:update` 覆盖开发态、检查、下载进度、就绪、安装门禁和错误状态。
- [x] Python 静态契约测试覆盖打包配置、资源路径、IPC/preload 和设置页入口。
- [ ] 完整 Python 测试和所有客户端 JavaScript 语法检查通过。
- [x] `npm run dist:win` 能生成 NSIS 安装包、`latest.yml` 和 blockmap。
- [x] 安装包内包含 `resources/workeventagent/`，Python bridge 在 packaged 模式从该目录启动后端。
- [ ] 代码经非作者 review 后才合入 `master`。

验证备注：F008 自身测试和打包已通过；当前 `master@9cef13a` 的全量 Python 回归仍有 30 个既有 F007 Phase B 失败。`feature/f007-phase-c-compendium@f1c6887` 的同一套回归为 `475 passed, 26 subtests passed`，说明主干在 F007 rebase/push 后丢失了 `gui.py` 的知识处理实现。该跨 feature 回归需要在合入 F008 前恢复。

## 发布操作

1. 更新 `client/package.json` 的语义化版本并提交。
2. 设置有 `repo` 写权限的 `GH_TOKEN`。
3. 在 `client/` 运行 `npm.cmd run release:win`。
4. 在 GitHub Release 中检查安装包、blockmap 和 `latest.yml` 后发布。

后续已安装的旧版本会在启动检查或用户手动检查时发现更高版本。
