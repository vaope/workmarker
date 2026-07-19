# F007 Phase C — Project Compendium Implementation Plan

> **For agentic workers:** Implement in order. Each task produces a testable unit.

**Goal:** Generate standalone project compendium for export/delivery. Deterministic assembly of all sources + optional AI-generated project introduction and cross-module conclusions.

**Architecture:** One new module `workeventagent/compendium.py`, plus tests and GUI handlers. No new dependencies.

## Task 1: Compendium backend module + tests

### Files
- Create `workeventagent/compendium.py`
- Create `tests/test_compendium.py`

### Implementation checklist

- [ ] `validate_module_conclusions(project_path)` — scan `<project_id>/docs/` for `doc_kind: project_module` files, check each has `## 模块结论` section
- [ ] `assemble_compendium(project_path, module_docs, ai_intro, ai_cross)` — deterministic assembly of full compendium Markdown
- [ ] `generate_source_manifest(project_path, module_docs)` — produce `sources.json` with file paths, hashes, module_ids
- [ ] `generate_compendium(project_path, workspace, opencode_bin, model)` — orchestrator: validate → optional AI → assemble → write
- [ ] Tests: validate module conclusions (valid, missing conclusion, no modules)
- [ ] Tests: deterministic assembly output structure matches spec
- [ ] Tests: source manifest covers all files exactly once
- [ ] Tests: full generate_compendium pipeline (no AI fallback)

## Task 2: GUI handlers

### Files
- Modify `workeventagent/gui.py`

- [ ] `handle_compendium_validate` — validate module conclusions, return status
- [ ] `handle_compendium_generate` — generate compendium (with optional AI)
- [ ] `handle_compendium_preview` — read generated compendium for preview
- [ ] `handle_compendium_list` — list existing exports for a project
- [ ] Register all 4 handlers in `_main_impl()`

## Task 3: Client UI

### Files
- Modify `client/windows/main.html` — add "Export Compendium" button in panorama toolbar
- Modify `client/windows/main.js` — add compendium IPC handlers and confirmation flow

## Task 4: Regression and doc update

- [ ] Full test suite: `python -m pytest tests/ -q`
- [ ] Update `docs/designs/F007-project-panorama.md` — mark Phase C complete
