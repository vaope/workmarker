---
feature_ids: [F007]
related_features: [F001, F002, F003, F004]
topics: [project-panorama, markdown, knowledge-governance, synthesis, migration]
doc_kind: design
created: 2026-07-13
updated: 2026-07-20
---

# F007 项目全景与知识治理

> 状态：co-creator 已批准；Phase A 已合入并验收；Phase B 已在隔离 feature branch 完成实现与合并前验收，等待独立 code review；Phase C 尚未解锁 | Phase B Owner：@cat-z8iqdgtj，Reviewer：@cat-gvxvam6r

## 为什么要做

WorkEventAgent 已经能够把工作进展保存到持久化 Timeline，并维护当前 Work Map。现在剩下的缺口是项目级理解：用户可以看到任务和事件，却还不能通过项目文档清楚地理解整个项目。

产品愿景是：

> 用户通过一个低摩擦入口记录日常细碎事实，WorkEventAgent 持续将这些事实转化成可信、可追溯的项目全景。

项目文档必须同时满足两个要求，而且不能制造两套真相：

- 人可以通过一个文档理解整个项目；
- 系统能够安全、确定性地更新这个文档。

外部产品调研与独立架构评审得出了相同的方向：一个简洁的总览、少量原子元数据、按需深入的补充材料、只能追加的事实证据，以及每个可写区块明确的所有权。

## 已确认的产品决策

co-creator 在 2026-07-12 的讨论中确认了以下决策：

1. 每个项目必须有一个默认文档，并且这个文档能够独立解释整个项目。
2. 用户继续通过一个入口记录信息，不需要在提交前判断它属于普通记录还是技术记录。
3. 明确事实可以通过现有可信工作流归档。
4. 推导出的项目结论必须展示证据和修改前后 diff，确认后才能写入。
5. 用户可以选择一个或多个事件，要求系统定向更新项目全景。
6. 高影响事件可以立即触发全景更新提案；普通事件只进入事实证据层。
7. 项目全景采用“结构化骨架 + 可读叙事”，而不是属性繁多的项目管理表单。
8. 项目可以有技术文档，但默认项目文档必须保留足够的技术背景，能够独立读懂。
9. 用户日常只阅读一个文档；其他文件只是可选细节，不能成为理解项目的前置条件。
10. 客户端增加一个独立、可配置的全局快捷键，用于显示或隐藏主窗口。它是独立的客户端增强，不与项目全景存储迁移耦合。
11. 项目结束或需要对外交付时，系统生成一份包含全部项目文字内容、并优先展示项目与模块结论的独立汇编文档。

## 产品边界

F007 强化的是项目记忆，而不是项目行政管理。

本功能不增加：

- 任务优先级；
- 截止日期；
- 负责人；
- 看板或甘特图；
- 额外的任务状态；
- AI 生成的每日优先级排序。

只有能够直接帮助回答以下问题的字段，才可以进入项目模型：

1. 这个项目是什么，为什么存在？
2. 项目现在处于什么位置？
3. 已经形成了哪些认知或决策？
4. 当前有什么风险或阻塞？
5. 下一步是什么？

## 单文档阅读契约

日常阅读契约非常简单：

> 打开项目的 `<project_id>.md`，不需要阅读其他文件就能理解项目。

一个第一次接触项目的人，应该在五分钟内回答：

- 项目背景、目标、范围和成功标准；
- 当前阶段和整体状态；
- 已完成的重要进展和当前重点；
- 高层技术架构；
- 关键认知与决策；
- 当前风险、阻塞、里程碑和下一步。

可选文件可以解释实现细节，但不能独占回答上述问题所必需的信息。默认项目文档必须保留充分的摘要，链接只用于深入阅读。

小项目可能永远不需要创建 `docs/` 目录。

### 物理目录布局

F007 不改变现有 Registry 的项目发现规则：每个项目仍然在 workspace 根目录下拥有一个 Markdown 文件。

```text
<workspace>/
  <project_id>.md                         # 唯一默认项目文档
  <project_id>/docs/<topic>.md            # 后续按需提议的可选细节
  attachments/                            # 现有附件存储
  reports/                                # 现有派生报告
  exports/<project_id>/<snapshot>/        # 独立交付的派生汇编
```

产品讨论中的 `project.md` 是“当前项目 `<project_id>.md`”的简称。F007 不会悄悄把所有项目迁移到新的文件夹模型。

## Project Document v2

### Frontmatter

Frontmatter 保持精简且原子化：

```yaml
---
project_id: workeventagent
title: WorkEventAgent
doc_kind: work_project
schema_version: 2
status: active
phase: project-knowledge-design
created: 2026-06-29
updated: 2026-07-13
---
```

规则：

- `status` 和 `phase` 是明确的项目级事实，不能根据任务是否完成自动推断。
- Frontmatter 不保存叙事性项目知识、嵌套任务数据、风险列表或技术架构。
- 客户端阅读视图可以隐藏 Frontmatter。

### 稳定区块锚点

解析器和写入程序使用稳定的 HTML 锚点，而不是依赖可见标题文字：

```markdown
## 项目档案 <!-- section:project-profile -->
## 当前全景 <!-- section:current-panorama -->
## 工作地图 <!-- section:work-map -->
## 技术概览 <!-- section:technical-overview -->
## 关键认知 <!-- section:project-knowledge -->
## 关键决策 <!-- section:decisions -->
## 附件 <!-- section:attachments -->
## 事件证据 <!-- section:timeline -->
## 历史摘要 <!-- section:rollups -->
```

未来即使翻译或修改可见标题，也不会改变解析语义。Section ID 一旦生成便不可修改。

### 人类可读的 Work Map

Schema v2 使用稳定标题和可见 checkbox 表示任务：

```markdown
### 工作项：统一捕获 <!-- item:unified-capture -->

让主窗口与快速捕获使用同一套持久化 Inbox 生命周期。

#### [x] 任务：主窗口先写 Inbox <!-- task:main-capture-inbox -->

- 下一步：补充解析完成通知
<!-- task-meta:last_event_id=20260712-main-capture-inbox -->
```

规则：

- `[ ]` 对应 `in_progress`，`[x]` 对应 `done`。
- `item_id` 和 `task_id` 锚点保持稳定。
- checkbox 和控制元数据由 wrapper 确定性渲染。
- 人类可读文档中不显示原始 `status: in_progress` 字段。
- 解析器依赖锚点和标题边界，不根据标题文字做模糊匹配。

### 完整示例

```markdown
# WorkEventAgent

> 将日常细碎进展持续转化为可信、可追溯的项目全景。

## 项目档案 <!-- section:project-profile -->

### 背景
工作进展散落在聊天、代码和临时笔记中，难以形成连续的项目认知。

### 目标
通过统一捕获，将日常事实持续整理成工作状态、历史证据和项目知识。

### 范围
本地优先；Markdown 是真相源；opencode 是唯一 LLM 执行入口。

### 成功标准
用户只读本文件即可理解项目，并能追溯每个推导结论的来源。

## 当前全景 <!-- section:current-panorama -->

项目已完成统一捕获、持久化 Inbox、搜索、纠错和工作地图。
当前重点是让 Agent 从事件中持续维护项目全景，同时不覆盖人工内容。

- 当前阶段：项目知识模型设计
- 最近成果：F004 工作地图已验收
- 当前风险：自动综合与人工编辑可能发生所有权冲突
- 下一步：落地 Project Document v2 和区块治理协议

<!-- panorama-meta:generated_at=2026-07-13T09:00:00+08:00;source_events=event-a,event-b -->

## 工作地图 <!-- section:work-map -->

### 工作项：项目知识体系 <!-- item:project-knowledge -->

#### [x] 任务：明确统一事件流 <!-- task:unified-event-flow -->

#### [ ] 任务：实现项目全景文档 <!-- task:project-panorama -->
- 下一步：完成 schema v2 迁移设计

## 技术概览 <!-- section:technical-overview -->

Electron 负责桌面交互与调度，Python 负责确定性归档，opencode 负责语义判断。
输入先进入持久化 Inbox，再异步生成归档或知识更新提案。

## 关键认知 <!-- section:project-knowledge -->

- 统一事件流是唯一事实入口。
- 项目全景是带来源的综合视图，不是第二份历史。

## 关键决策 <!-- section:decisions -->

- 2026-07-13：采用单文档全景和区块级所有权。

## 附件 <!-- section:attachments -->

## 事件证据 <!-- section:timeline -->

## 历史摘要 <!-- section:rollups -->
```

## 区块所有权契约

真正的冲突不是“人类文档与 Agent 文档之间的冲突”，而是同一个区块存在两个不受约束的写入者。因此，v2 的每个区块必须属于一种明确的修改类型。

| 区块 | 修改类型 | 写入规则 |
|---|---|---|
| 项目档案 | `reviewed` | 人可以直接编辑；Agent 只能提出 diff |
| 当前全景 | `derived-reviewed` | 整段或按固定子区块生成；展示来源和 diff 后才能写入 |
| 工作地图 | `structured` | 只能使用现有 typed data 和确定性 renderer |
| 技术概览 | `reviewed` | Agent 提案 + 证据 + 用户确认 |
| 关键认知 | `reviewed` | Agent 提案 + 证据 + 用户确认 |
| 关键决策 | `append-only` | 明确决策可以追加；推导出的决策必须确认 |
| 附件 | `append-only` | 使用现有附件协议 |
| Timeline | `append-only` | 使用现有事件与纠错协议 |
| 历史摘要 | `derived` | 确定性报告或综合流程可以重生成 |

客户端必须在视觉上区分：

- 可以重新生成的派生内容；
- Agent 修改前必须经过批准的人类控制内容；
- 只能追加的事实证据。

任何后台操作都不得静默覆盖 `reviewed` 区块。

## 写入架构

F007 不建设通用 Markdown AST，也不建设依赖模糊语义匹配的 patch 引擎。

写入分为四条确定性轨道：

1. **结构化轨道**：Work Map 和 Timeline 继续使用 typed JSON 与确定性渲染。
2. **派生轨道**：生成稳定锚点之间的完整内容；AI 派生变更形成待确认提案，确认后才能替换。
3. **审阅轨道**：Agent 返回 typed proposal；客户端展示证据和 before/after diff；wrapper 只应用已经确认的区块替换。
4. **追加轨道**：向目标区块追加确定性渲染的记录。

Agent 的叙事输出可以包含受限的段落或列表内容，但不能包含区块标题、稳定锚点、HTML 注释或文件路径。文档结构和控制元数据始终由 wrapper 生成。

### 知识更新提案

```json
{
  "project_id": "workeventagent",
  "target_section": "technical-overview",
  "operation": "replace_section_content",
  "base_section_hash": "sha256:...",
  "reason": "主窗口与快速捕获已经统一使用持久化 Inbox 生命周期。",
  "source_event_ids": [
    "20260712-main-capture-inbox",
    "20260712-quick-capture-inbox"
  ],
  "content": {
    "paragraphs": ["..."],
    "bullets": ["..."]
  }
}
```

在应用任何非追加提案前，wrapper 必须：

1. 验证项目和目标区块锚点；
2. 验证所有来源事件真实存在；
3. 将 `base_section_hash` 与当前区块比较；
4. 提案过期时拒绝写入；
5. 渲染 before/after diff；
6. `reviewed` 区块必须经过确认；
7. 原子写入 Markdown；
8. 重新解析文档并重建 SQLite。

## 从事件到项目知识

```text
用户提交一条记录
  -> 持久化 Inbox 卡片和原始证据
  -> 通过现有 F003 可信工作流完成路由与归档
  -> 判断知识影响
     -> 普通事实：不触发全景综合
     -> 明确任务事实：更新 Work Map + Timeline
     -> 明确决策：追加 Decision + Timeline
     -> 推导出的项目或技术结论：创建 reviewed proposal
     -> 高影响事件：提供即时全景更新提案
```

支持以下综合触发方式：

- 目标、范围、架构、风险或里程碑发生高影响变化后，立即生成提案；
- 用户从 Timeline 或 Search 选择一个或多个事件，手动发起定向综合；
- 当客户端或托盘进程运行时，每日生成一个待确认的 Current Panorama 提案；
- 复用现有 F002 调度基础，每周执行一次完整全景审查。

普通捕获不得触发完整项目综合。

## 技术文档

默认项目文档始终包含足够完整的技术概览。

只有满足以下条件时，系统才可以提议创建 `<project_id>/docs/architecture.md` 等可选文档：

- 实现细节已经无法在少量段落中清晰概括；
- 内容需要独立生命周期或独立读者；
- 用户确认创建。

创建提案必须展示：

- 建议文件名与用途；
- 默认项目文档中仍然保留的技术摘要；
- 来源 event IDs；
- 新文档初始 diff。

Agent 不得自动创建一整棵技术文档树。可选文档用于深入项目，而不是弥补项目全景缺失的信息。

### 可汇编模块文档契约

需要进入最终项目汇编的子文档必须明确声明自己的项目归属和模块身份：

```yaml
---
doc_kind: project_module
project_id: workeventagent
module_id: capture-inbox
title: Capture Inbox
order: 10
include_in_compendium: true
updated: 2026-07-13
---
```

每个可汇编模块至少包含：

```markdown
## 模块结论 <!-- section:module-conclusion -->

这一模块最终解决了什么问题、形成了什么结论，以及对整个项目有什么影响。

## 详细内容 <!-- section:module-body -->

完整设计、实现说明和验证证据。
```

规则：

- 汇编器只读取 `doc_kind: project_module`、`project_id` 匹配且 `include_in_compendium: true` 的文件，不能把目录中的任意 Markdown 自动纳入。
- `module_id` 在同一项目内唯一且稳定；显示标题可以修改。
- `order` 决定模块在汇编文档中的顺序；相同值按 `module_id` 排序。
- 缺少“模块结论”时不能发布正式汇编。系统应先生成补充结论的 reviewed proposal，由用户确认后再继续。
- 子文档的正文仍然是项目真相源的一部分；最终汇编只是它的只读快照。

## Timeline 的物理位置

Phase A 继续把 Timeline 保存在 `<project_id>.md` 内，因为报告、搜索、纠错和现有解析器都依赖文档内的 Timeline 区块。

客户端默认折叠 Timeline，因此它不会占据日常阅读的主要空间。Markdown 阅读器也可以按标题折叠这个区块。

只有在独立设计并充分测试既有 split rule 后，才能实施 Timeline 物理拆分。不能为了渲染效果而直接改变存储契约。

## Schema v1 → v2 迁移

旧项目在迁移前必须继续可读。

迁移必须显式触发并且可以预览：

1. 检测 `schema_version` 缺失或等于 `1`。
2. 解析现有必需区块和稳定 ID。
3. 在内存中生成 v2 文档。所有无法识别的内容必须逐字节保留；如果非标准任务块无法安全转换，立即停止，不能猜测。
4. 展示迁移摘要和完整 diff。
5. 用户确认后，在 `.workeventagent/backups/<project_id>/` 下写入带时间戳的备份。
6. 原子替换项目文件。
7. 重新解析并验证全部 project/item/task/event ID 以及 Timeline 事件数量。
8. 重建 SQLite。

迁移必须幂等，v2 文档不得再次迁移。

任何锚点、事件或区块无法完整保留时，迁移返回可见错误，不得替换原文件。

Phase A 发布后，新项目直接使用 schema v2。

## 客户端体验

项目工作区变成一个统一的“项目全景”阅读界面，同时继续保持 F004 对 Work Map 的强调：

- 项目档案与当前全景显示在最前面；
- Work Map 保持直接可见和可交互；
- 后面依次展示技术概览、关键认知、关键决策和历史区块；
- 项目档案、技术概览和关键认知提供明确的应用内手动编辑入口；
- Timeline 和历史摘要默认折叠；
- 渲染视图隐藏来源控制元数据和稳定锚点；
- 每个 `derived`/`reviewed` 区块都提供“查看来源”；
- `reviewed` 区块显示“审阅提议变更”，而不是允许后台静默修改。

Reports、Search、Inbox、Settings 和 correction 仍然是独立的应用工具，但理解当前项目不需要打开它们。

## 失败与冲突处理

- Agent 综合失败时，当前项目文档保持不变。
- 来源事件缺失时，提案无效。
- 区块 hash 已变化时，提案过期，必须重新生成。
- 迁移失败时保留原文件和备份状态。
- Markdown 写入成功但 SQLite 更新失败时，可以通过重建索引恢复。
- 现有纠错规则继续保留历史证据。
- MVP 仍然采用单写者假设；外部并发编辑通过非追加写入前的 section hash 检测。

## 交付阶段

### Phase A：可读、可治理的文档基础

- schema v2 parser 和 renderer；
- 稳定区块锚点与人类可读的 Work Map；
- 带备份、diff 和验证的显式 v1 迁移；
- 客户端项目全景阅读界面；
- `reviewed` 区块的应用内手动编辑和过期内容检测；
- 区块所有权标识和来源入口；
- 保证 capture、reports、search、correction 和 index 的兼容性。

Phase A 不要求新增 LLM 综合。先建立安全的文档模型，再接入自动综合。

### Phase B：项目知识综合

- 事件影响分类；
- Current Panorama 生成；
- 从选定事件进行定向综合；
- Technical Overview 和 Project Knowledge 的 reviewed proposal；
- 证据验证、section hash 和 diff 确认；
- 高影响、每日和每周触发器；
- 可选技术文档创建提案。

#### 2026-07-20 实施与合并前验收记录

Phase B 按已独立评审放行的计划 `docs/superpowers/plans/2026-07-19-project-panorama-phase-b.md` 在 `feature/f007-complete-phase-b` 实现。实现提交为 `67455eb..ee05f70`；本节只记录 feature branch 的合并前证据，不代表独立 code review 或 merge gate 已完成。

- 持久层：job / proposal / schedule-run 均为逐实体、TTL=0、CAS/version 的原子账本；启动恢复覆盖 source commit、section bundle 与 module document 的 base/target/conflict 三分支。
- 触发层：普通捕获不创建完整综合任务；高影响任务在事实写入前以 `awaiting_source` 落盘；定向、每日和每周入口统一进入 durable job。
- 周期边界：schedule-run manifest 在 enqueue 前固定完整 project/child 清单，失败 child 仅阻止完成，重试后按全部 expected child IDs 重算。
- 提案边界：agent 只返回内容与影响判断；wrapper 注入 project/source/proposal/hash/module identity。多区块 bundle 确认后 immutable，全有或全无、单次原子替换并回读验证。
- 模块文档：wrapper 派生并防撞 `module_id`、filename、order；创建前要求 Technical Overview 的 retained summary 已确认并仍存在于主文档。
- 客户端：项目全景与 Search 可选择真实 Timeline 事件；Inbox 聚合显示独立持久化的知识任务/提案；证据、影响维度、before/after、diff、修订/拒绝/重试/过期重生及独立文档确认均可达。
- 自动验证：`343 passed, 19 subtests passed`；11 个 client JavaScript 文件通过 `node --check`；真实 opencode 1.18.1 synthesizer bounded-JSON smoke 通过。
- Electron 验收：隔离 workspace/userData、1040×700，12/12 通过，覆盖全景入口、纵向滚动契约、Timeline 选择、统一审核、证据/diff、恶意 HTML 转义、整包确认、高影响 badge、无横向溢出、Search 定向入口、每日/每周设置以及无自动应用。
- 验收期间发现并修复既有 Python bridge 入口缺失：`client/python_bridge.js` 使用 `python -m workeventagent.gui`，而模块此前未调用 `main()`；已补子进程回归测试，真实 Electron→Node→Python 链恢复。

Phase B 只有在跨个体 code review、merge gate 和 merged-master 隔离验收全部通过后才算交付；在此之前不得据此解锁 Phase C。

### Phase C：项目汇编与独立发布

Phase C 为需要离开 WorkEventAgent 独立交付的场景生成“项目完整汇编”。它不是把文件简单连接，也不是让 AI 重新发明项目内容。

#### 来源

汇编器只使用以下显式来源：

1. 当前项目 `<project_id>.md`；
2. `<project_id>/docs/` 下符合“可汇编模块文档契约”的文件；
3. 这些文档引用的附件清单和来源 event IDs。

日报、周报、旧项目总结和历史汇编文件都是派生产物，不得再次作为汇编输入，避免递归和重复统计。

来源覆盖报告还必须列出 `<project_id>/docs/` 中发现但未纳入的 Markdown 及排除原因，避免项目内容因为缺少 frontmatter 而被静默遗漏。

#### 生成方式

```text
验证所有来源与模块结论
  -> 对来源文件计算内容 hash
  -> 确定性提取项目档案、当前/最终全景、技术概览、决策和模块结论
  -> 按 order 确定性汇编全部模块正文
  -> AI 只生成项目总论和跨模块结论，并引用来源 section/module IDs
  -> 生成来源覆盖报告
  -> 客户端预览
  -> 用户确认发布
  -> 原子写入独立 Markdown
```

确定性层负责“一个来源也不能漏”；AI 层只负责“如何把已确认结论讲清楚”。AI 失败时仍可生成内容完整的确定性汇编，但必须明确标注“跨模块 AI 综合不可用”，不能伪造项目总论。

#### 输出结构

```markdown
# WorkEventAgent 项目完整文档

## 项目总论
## 项目背景、目标与范围
## 项目状态与工作地图
## 项目整体架构
## 模块结论
### Capture Inbox
### Global Search
### Correction Workflow
### Reporting
## 跨模块关键结论
## 关键决策、风险与经验
## 模块完整内容
### Capture Inbox 完整内容
### Global Search 完整内容
...
## 事件证据与附件附录
## 来源与覆盖索引
```

前半部分优先回答“整个项目和每个模块最终得出了什么结论”，后半部分确定性收录全部模块正文、项目 Timeline 和附件索引，兼顾阅读效率与内容完整性。

#### 输出契约

主输出路径：

```text
<workspace>/exports/<project_id>/<YYYYMMDD-HHmmss>/<project_id>-compendium.md
```

输出 frontmatter：

```yaml
---
doc_kind: project_compendium
project_id: workeventagent
generated_at: 2026-07-13T15:30:00+08:00
generator_version: F007-C
source_file_count: 5
module_count: 4
source_manifest: sources.json
editable_source: false
---
```

同目录的 `sources.json` 保存每个来源文件的相对路径、内容 hash、`module_id` 和纳入的 section IDs。引用的本地附件可以复制到同一 snapshot 的 `assets/` 并重写为相对链接；无法复制的附件必须进入缺失资源报告。

Markdown 是 Phase C 的必需交付格式。PDF 可以从同一份已确认 Markdown 渲染，但只是展示适配器；PDF 生成失败不得影响 Markdown 汇编，也不得引入第二套内容生成逻辑。

后端使用独立的 `generate_compendium` 契约和独立汇编模块。它可以复用 F002 的原子写入、预览和 AI runner 基础，但不能把 Timeline-only 的 `project_summary` 改名冒充完整汇编。

#### 真相源边界

- 汇编文件可以发送、归档和人工批注，但 WorkEventAgent 不从它反向更新项目。
- 项目内容需要修改时，修改 `<project_id>.md` 或对应模块文档，然后重新生成汇编。
- Registry、知识综合和下一次汇编必须排除 `doc_kind: project_compendium`。
- Search 若索引汇编文件，必须标记为“派生发布物”，不能把它与项目真相源结果混在一起。
- 相同来源 hash 和相同生成配置必须产生内容相同的确定性正文；`generated_at` 和 AI 总论不参与该正文一致性判断。

### 独立客户端增强

主窗口全局快捷键单独实现和评审：

- 使用一个与快速捕获不同的、可配置的 accelerator；
- 第一次触发时显示主窗口并聚焦；
- 第二次触发时隐藏到托盘；
- 注册冲突时保留上一个有效快捷键并显示错误；
- 不修改项目文档或综合契约。

## 验收标准

### Phase A

- v2 `<project_id>.md` 无需打开其他文件即可满足全部六项阅读结果。
- Frontmatter 保持精简，不包含叙事性项目知识。
- 可见标题适合人类阅读，解析器使用稳定 section ID。
- Work Map 可以继续通过现有客户端操作编辑，文档正文不显示原始状态代码。
- Timeline 保持 append-only，报告、搜索和纠错行为不变。
- 客户端隐藏控制元数据，并默认折叠 Timeline/历史摘要。
- v1 迁移保留全部 project/item/task/event ID、附件记录、决策和 Timeline 事件。
- 迁移失败不会产生部分替换。
- 可以从迁移后的 Markdown 成功重建 SQLite。

### Phase B

- 普通捕获事件不会触发完整全景综合。
- 高影响事件可以生成带证据的即时提案。
- 用户可以选择事件并要求定向更新全景。
- `derived` 区块提案经过确认后，可以在不修改 reviewed 或 append-only 区块的情况下写入。
- `reviewed` 区块提案在确认前展示来源事件和 before/after diff。
- section hash 过期时阻止写入。
- 不能仅根据任务是否完成推断项目 status 或 phase。
- 未经确认不得创建可选技术文档。

### Phase C

- 发布前验证全部纳入模块都有稳定 `module_id` 和经过确认的“模块结论”。
- 汇编器只包含显式匹配的项目与模块文档，并生成逐文件来源覆盖报告。
- 每个符合条件的来源文件在 `sources.json` 中恰好出现一次。
- 项目 Work Map、Timeline 和附件索引均进入完整汇编，不能只汇编项目摘要与模块文档。
- `<project_id>/docs/` 中未被纳入的 Markdown 会显示在覆盖报告中，并说明排除原因。
- 项目与模块结论显示在完整正文之前。
- 模块完整正文通过确定性汇编进入输出，不能由 AI 摘要替代。
- AI 生成的项目总论和跨模块结论引用来源 section/module IDs。
- AI 失败时仍可生成完整 Markdown，并显示清晰的降级说明。
- 汇编生成不修改任何项目源文档。
- 下一次汇编不会读取旧汇编、日报、周报或项目总结。
- 输出 Markdown 离开 WorkEventAgent 后仍可独立阅读；缺失附件必须显式列出。

## 非目标

- 通用 Markdown 编辑器或 semantic patch 引擎。
- 使用数据库替代 Markdown 真相源。
- 第二份可编辑的人类阅读版项目文件。
- 在本功能内自动拆分 Timeline 或 Item。
- 属性繁多的项目管理系统。
- 自动重写人类控制的项目目标或技术原则。
- 多用户或多写者合并解决方案。
- 将发布汇编反向导入为项目事实。
- 使用 AI 摘要替代模块完整正文。

## 风险与缓解措施

1. **派生内容与人工内容发生碰撞。** 使用区块所有权、稳定锚点、hash 和客户端标识明确边界。
2. **Schema 迁移破坏历史证据。** 必须提供预览、备份、原子替换、ID/事件数量验证和 golden migration tests。
3. **项目全景变成另一份不可信的 AI 摘要。** 每个推导结论都携带来源事件；所有推导修改都必须展示 diff 并确认。
4. **项目文档变成项目管理表单。** 元数据保持精简，丰富理解来自叙事综合，而不是新增任务字段。
5. **一个功能扩张成多个系统。** Phase A 只建立文档基础，Phase B 增加综合，Phase C 最后增加独立发布；主窗口快捷键保持独立。
6. **发布汇编变成第二真相源。** 输出携带来源 manifest 和 hash，明确禁止反向导入，并从 Registry 与后续综合输入中排除。
7. **“完整”与“可读”互相冲突。** 结论优先显示，完整正文放在后半部分；确定性层保证不漏内容，AI 层只改善总论和跨模块关系。

## 实施交接

co-creator 批准本设计文档后：

1. 编写 Phase A 实施计划；
2. 在隔离 feature worktree 中按照 TDD 实现 Phase A；
3. 执行独立规格与代码评审；
4. 合并前完成运行时验收；
5. 只有在迁移文档和阅读界面验收通过后，才规划 Phase B；
6. 只有在项目全景和模块结论治理通过验收后，才规划 Phase C 汇编发布。
