**Overview**
- Purpose: Manage write/deliver pipelines via SQLite so writers and deliveries are configurable without editing scripts.
- Location: tables live in `data/info.db`; tools in `news-collector/write-deliver-pipeline/`.
- Writers used: `info_writer.py`, `wenhao_writer.py`, `feishu_writer.py` (existing). Deliveries use `deliver/mail_deliver.py` and `deliver/feishu_deliver.py`.

**Schema Summary**
- `pipelines`: name, enabled, description, timestamps.
- `pipeline_filters`: category/source selectors with all-or-whitelist controls (`all_categories`, `categories_json`, `all_src`, `include_src_json`).
- `pipeline_writers`: `type` (info_html | email_html | feishu_md), `hours`, optional `weights_json` (metric-key → weight), `bonus_json`.
- `pipeline_writer_metric_weights`: normalized per-pipeline metric weights (preferred over JSON).
- `pipeline_deliveries_email`: single recipient email + `subject_tpl`. One row per pipeline.
- `pipeline_deliveries_feishu`: Feishu card delivery with `app_id`, `app_secret`, `to_all_chat` (1=all groups, 0=use `chat_id`), optional `title_tpl`, `to_all`, `content_json`. One row per pipeline.
- Constraint: a pipeline must have exactly one delivery, in either email table or Feishu table.

**Output Rules**
- Directory per pipeline: `data/output/pipeline-<pipeline_id>`.
- Filename: `${ts}.html` for email, `${ts}.md` for Feishu; `ts=YYYYMMDD-HHMMSS`.
- Runner computes the full path and passes `--output` to writers. It also sets `PIPELINE_ID` for subprocesses so writers/deliveries self-fetch configuration from DB by default.

**Admin Commands**
- Initialize tables: `python news-collector/write-deliver-pipeline/pipeline_admin.py init`
- Seed sample pipelines: `python news-collector/write-deliver-pipeline/pipeline_admin.py seed`
- Import JSON: `python news-collector/write-deliver-pipeline/pipeline_admin.py import --input data/pipelines/all_settings.json --mode replace`
- Export one pipeline: `python news-collector/write-deliver-pipeline/pipeline_admin.py export --name <pipeline_name> --output data/pipelines/export.json`
- Export all: `python news-collector/write-deliver-pipeline/pipeline_admin.py export --all --output data/pipelines/export.json`
- List pipelines: `python news-collector/write-deliver-pipeline/pipeline_admin.py list`
- Enable: `python news-collector/write-deliver-pipeline/pipeline_admin.py enable <name>`
- Disable: `python news-collector/write-deliver-pipeline/pipeline_admin.py disable <name>`

**Runner Commands**
- Run by name: `python news-collector/write-deliver-pipeline/pipeline_runner.py --name <pipeline_name>`
- Run all enabled (sequential): `python news-collector/write-deliver-pipeline/pipeline_runner.py --all`
- Uses current Python (`PYTHON` env var or `sys.executable`) to call writers/deliveries.

**Writers**
- Email writer: `writer/email_writer.py`
  - Generates HTML digest; applies per-source bonus, category limits, etc.
  - If AI scoring is available, reads from `ai_metrics` + `info_ai_scores` to compute weighted scores；否则仅按时间排序。
- Feishu writer: `writer/feishu_writer.py`
  - Generates Feishu-friendly Markdown; requires AI评分（`ai_metrics` + `info_ai_scores`）。
- DB-driven defaults: when `PIPELINE_ID` is present, writers read hours/categories/weights/bonus from DB. Weights precedence: `pipeline_writer_metric_weights` > `pipeline_writers.weights_json` > `ai_metrics.default_weight`. CLI flags still override for ad-hoc runs.

**Delivery Config**
- Email: `deliver/mail_deliver.py` used by runner with `--html` only; when `PIPELINE_ID` is present it reads recipient and subject template from DB.
- Feishu (card): `deliver/feishu_deliver.py` used by runner with `--file --as-card`; when `PIPELINE_ID` is present it reads credentials and target (to_all/chat_id) and title template from DB.
- Pipeline DB fields: Email uses `email` + `subject_tpl`; Feishu uses `app_id`/`app_secret` + `to_all_chat` or `chat_id` + `title_tpl`/`to_all`.
- Security: do not commit real secrets; prefer environment variables for local runs. The seed/import flow accepts values for convenience but treat them as sensitive.

**JSON Import/Export Format**
- Root: `{ "version": 1, "pipelines": [ ... ] }`
- Item fields:
  - `pipeline`: `{ "id?", "name", "enabled", "description" }` (`id` optional; exported for reference)
  - `filters`: `{ "all_categories", "categories_json", "all_src", "include_src_json" }` (arrays or JSON strings accepted)
  - `writer`: `{ "type", "hours", "weights_json", "bonus_json" }` (objects or JSON strings accepted; `weights_json` keys must be metric keys defined in `ai_metrics`)
  - `delivery`:
    - Email: `{ "kind": "email", "email": "a@b.com", "subject_tpl": "${date_zh}整合" }`
    - Feishu: `{ "kind": "feishu", "app_id": "...", "app_secret": "...", "to_all_chat": 1, "chat_id": null, "title_tpl": "通知", "to_all": 1, "content_json": null }`
- Import behavior:
  - Explicit zeros are respected (e.g., `enabled: 0`, `all_categories: 0`).
  - Export includes `pipeline.id` to aid debugging and optional matching during import.
  - When importing, if a valid `pipeline.id` exists in the DB, it is used (and name updated if needed); otherwise, matching falls back to `pipeline.name`.
- Import modes:
  - `replace`: clears existing child rows for same-name pipeline then inserts (recommended when syncing from JSON).
  - `merge`: upserts without clearing; ensures only one delivery table is used.

**Scheduling**
- Simple loop script: `scripts/auto-pipelines-930.sh`
  - Ensures schema and seeds defaults
  - Runs collector → evaluator (40h) → pipelines (sequential)
  - Sleeps until next 09:30 and repeats
- For ad-hoc run without waiting, execute the admin/import + collector + evaluator + runner commands directly.

**Gotchas**
- Missing deps: collectors require `feedparser`/`beautifulsoup4`; install with `pip install -r requirements.txt`.
- AI evaluator requires `AI_API_BASE_URL`, `AI_API_MODEL`, `AI_API_KEY`. Without it, `ai_metrics`/`info_ai_scores` won’t be populated; runners skip writers that need AI scores.
- Single-delivery rule: each pipeline must have exactly one delivery (email or Feishu). Both present → runner fails.
- Secrets: keep `app_secret` out of VCS; use environment overrides during runtime when possible.

**Examples**
- Seeded pipelines after `seed` (IDs vary):
  - email_306483372 → `info_html` 40h → `306483372@qq.com` subject `${date_zh}整合`
  - email_410861858_wenhao → `wenhao_html` 24h → `410861858@qq.com` subject `HW精选`
  - feishu_broadcast → `feishu_md` 40h → Feishu card to all groups (requires valid app credentials)
