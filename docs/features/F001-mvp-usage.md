---
feature_ids: [F001]
topics: [usage, workeventagent, cli]
doc_kind: guide
created: 2026-06-29
---

# F001 MVP Usage

WorkEventAgent captures one-sentence work updates and archives them into a project Markdown document with a rebuildable SQLite index.

## Prerequisites

- Python 3.11+
- `opencode` CLI on PATH
- A project Markdown document following `docs/WORKLOG_SCHEMA.md`

## Quick Start

### 1. Set up a project

Create a project document (or use the fixture):

```bash
cp tests/fixtures/multimodal-labeling.md projects/multimodal-labeling.md
```

### 2. Dry run — preview before writing

```bash
python -m workeventagent.cli capture \
  --project projects/multimodal-labeling.md \
  --db .workeventagent/index.sqlite \
  --text "Reviewed blockers for KV cache few-shot optimization today." \
  --dry-run
```

Expected output shows a confirmation card with:

```
Archive proposal
  project_id:  multimodal-labeling
  item_id:     kv-cache-few-shot
  task_id:     kv-cache-blockers
  confidence:  0.91
  reason:      Matched KV cache item.
  ...
  confirm / edit / cancel
>
```

### 3. Confirm and write

```bash
python -m workeventagent.cli capture \
  --project projects/multimodal-labeling.md \
  --db .workeventagent/index.sqlite \
  --text "Reviewed blockers for KV cache few-shot optimization today."
```

Type `confirm` at the prompt. Expected:

```
Markdown written
SQLite index updated
```

### 4. Attach files (MVP — path only)

```bash
python -m workeventagent.cli capture \
  --project projects/multimodal-labeling.md \
  --db .workeventagent/index.sqlite \
  --text "Reviewed blockers." \
  --attach screenshots/baseline.png
```

Paths are recorded in the `## Attachments` section. File bytes are not copied in MVP.

### 5. New task creation

When the agent detects a new task (`new_task: true`), the confirmation card shows the exact Markdown block to insert with a wrapper-generated stable ID. Confirm to create.

### 6. Edit before confirming

Type `edit` at the prompt to open the proposal in your `$EDITOR` (or `notepad` on Windows). Modify and save. The card re-renders for re-confirmation.

### 7. Cancel

Type `cancel` (or `Ctrl+C`) to exit without writing. Any unknown input defaults to cancel.

## Interaction Contract

| Input | Behavior |
|---|---|
| `confirm` | Write Markdown → update SQLite → exit 0 |
| `edit` | Open editor → re-render card → prompt again |
| `cancel` / `Ctrl+C` / anything else | Exit 2, no writes |

Low-confidence proposals (`confidence < 0.6`) are rejected automatically — no card shown, exit 1.

## What Gets Written

On confirm, a **single** Markdown write (atomic via temp file + `os.replace`) updates:

1. **Work Map** — targeted task block `status`, `next_action`, `last_event_id`
2. **Timeline** — append-only event with timestamp, summary, and task reference
3. **Frontmatter** — `updated` date bumped
4. **Attachments** — path records appended (if `--attach` was used)

Then SQLite index is rebuilt from Markdown.

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Confirmed and written |
| 1 | Error (agent failed, low confidence, parse error) |
| 2 | Cancelled by user |

## Rebuilding SQLite

SQLite is always rebuildable from Markdown:

```python
from workeventagent.index_store import init_db, rebuild_index, get_task

init_db(db_path)
rebuild_index(db_path, [project_path])
task = get_task(db_path, "kv-cache-blockers")
```

## Next Steps After MVP

- Multi-project `capture` without `--project` (auto-detect from Git root)
- Daily/weekly rollup regeneration
- Correction events for fixing wrong archives
- File copy on `--attach` instead of path-only
- Concurrent write safety (locking)
