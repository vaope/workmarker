---
project_id: report-project
title: 报表项目
doc_kind: work_project
schema_version: 2
status: active
phase: implementation
created: 2026-07-13
updated: 2026-07-13
---
# 报表项目

## 项目档案 <!-- section:project-profile -->

### 背景

### 目标

### 范围

### 成功标准

## 当前全景 <!-- section:current-panorama -->

## 工作地图 <!-- section:work-map -->

### 工作项：Capture <!-- item:capture -->

Durable intake.

#### [x] 任务：Persist card <!-- task:persist-card -->
- 下一步：Add retry.
<!-- task-meta:last_event_id=event-a -->

#### [ ] 任务：Route archive <!-- task:route-archive -->
- 下一步：Wire inbox lifecycle.
<!-- task-meta:last_event_id=event-b -->

### 工作项：Reporting <!-- item:reporting -->

Report generation.

#### [ ] 任务：Weekly summary <!-- task:weekly-summary -->
- 下一步：Schedule cron.
<!-- task-meta:last_event_id= -->

## 技术概览 <!-- section:technical-overview -->

Python 负责确定性写入。

## 关键认知 <!-- section:project-knowledge -->

- 统一事件流是唯一事实入口。

## 关键决策 <!-- section:decisions -->

- 2026-07-13：采用单文档全景和区块级所有权。

## 附件 <!-- section:attachments -->

- attachments/2026-07-13/screenshot.png <!-- attachment:att-001 -->
  - note: Initial setup screenshot.

## 事件证据 <!-- section:timeline -->

- 2026-07-13T10:00:00+08:00 <!-- event:event-a -->
  - task_id: persist-card
  - input: Finished persistence.
  - summary: Persistence is complete.
  - status: done
  - next_action: Add retry.

- 2026-07-13T11:00:00+08:00 <!-- event:event-b -->
  - task_id: route-archive
  - input: Started archive routing.
  - summary: Archive routing in progress.
  - status: in_progress
  - next_action: Wire inbox lifecycle.

## 历史摘要 <!-- section:rollups -->
