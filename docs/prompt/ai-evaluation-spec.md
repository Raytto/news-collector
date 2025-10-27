# AI Article Evaluation Integration Spec

## 1. Background & Goals
- **Objective**: Introduce an automated AI-powered evaluation pipeline that produces multi-dimensional recommendation scores, summaries, and review snippets for each article record stored in the `info` table.
- **Scope**: Design configuration, data schema, prompting strategy, and orchestration scripts. No implementation yet—this document provides the blueprint.

## 2. High-Level Architecture
1. **Data Source**: Existing `info` table rows that have associated `detail` content.
2. **AI Evaluation Service**: An LLM provider accessed through a configurable HTTP API client. All endpoint URLs, model names, and API keys are read from the root-level `environment.yml` via Conda environment variables.
3. **Prompt Layer**: Prompts live in dedicated files (one per task) under `prompts/ai/`. The evaluation script loads prompts at runtime, interpolates article metadata/content, and submits them to the AI service.
4. **Result Storage**: A new database table (`info_ai_review`) stores scores, weighted recommendations, and AI feedback linked to the originating `info.id`.
5. **Orchestration Script**: A manager command iterates over all `info` rows missing AI evaluations, requests scores, and writes results back.

## 3. Configuration & Secrets
- Add environment variables to the Conda `environment.yml` (example names below; final values supplied externally):
  - `AI_API_BASE_URL`
  - `AI_API_MODEL`
  - `AI_API_KEY`
  - Optional tuning knobs (rate limits, temperature) may also be defined here via variables such as `AI_REQUEST_INTERVAL`, `AI_API_TIMEOUT`, or custom weight overrides.
- The runtime code must read these variables using the existing configuration helper (if present) or `os.getenv`.
- Never commit real keys. Document required variables in `docs/prompt/ai/README.md` (future task).

## 4. Prompt File Structure
- Create `prompts/ai/` directory to store prompt templates (kept close to executable scripts rather than documentation).
- Store the evaluation template in `prompts/ai/article_evaluation_zh.prompt`.
- The template should instruct the model to:
  1. Review the provided `detail` text.
  2. Produce 1–5 scores for each recommendation angle (see §5).
  3. Summarize the article in a single sentence.
  4. Provide a one-sentence qualitative evaluation.
  5. Return JSON with explicit fields for each score and message.
- Include placeholder tokens (e.g., `{{title}}`, `{{detail}}`) to be substituted by the script.
- All prompt instructions, the resulting summary, and the qualitative evaluation must be written in Simplified Chinese to align with downstream presentation.

## 5. Evaluation Dimensions & Weighting
- Define a canonical set of recommendation angles (customizable later):
  - `timeliness`
  - `relevance`
  - `insightfulness`
  - `actionability`
- Each score ranges from 1 (poor) to 5 (excellent).
- Compute the final recommendation score as a weighted average. Initial weights:
  - `timeliness`: 0.25
  - `relevance`: 0.35
  - `insightfulness`: 0.25
  - `actionability`: 0.15
- Store raw scores and final value in the database with precision that supports decimals (e.g., `NUMERIC(3,2)`).
- Keep weights configurable via environment variables defined in `environment.yml` or a config module to allow future tuning.

## 6. Database Changes
- Add migration to create `info_ai_review` table with the following columns:
  | Column | Type | Constraints | Notes |
  | --- | --- | --- | --- |
  | `id` | INTEGER | PRIMARY KEY, references `info.id` | Shares IDs with `info`; ensures 1:1 relation. |
  | `final_score` | NUMERIC(3,2) | NOT NULL | Weighted average of angle scores. |
  | `timeliness_score` | SMALLINT | NOT NULL | 1–5 inclusive. |
  | `relevance_score` | SMALLINT | NOT NULL | 1–5 inclusive. |
  | `insightfulness_score` | SMALLINT | NOT NULL | 1–5 inclusive. |
  | `actionability_score` | SMALLINT | NOT NULL | 1–5 inclusive. |
  | `ai_summary` | TEXT | NOT NULL | One-sentence summary. |
  | `ai_comment` | TEXT | NOT NULL | One-sentence qualitative evaluation. |
  | `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Audit trail. |
  | `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | For future updates (trigger can auto-update). |
- Ensure a unique constraint on `id` to prevent duplicates.
- Consider indexes on `final_score` for faster ranking queries.

## 7. Manager Script Responsibilities
- Location: `manager/ai_evaluate.py` (new file).
- Key steps:
  1. Initialize database session/connection using existing helpers.
  2. Fetch `info` rows where no matching `info_ai_review` row exists.
  3. For each row:
     - Load the evaluation prompt template.
     - Substitute placeholders with article metadata (`title`, `source`, `detail`, etc.).
     - Call the AI API with proper retry logic and rate-limiting compliance.
     - Parse JSON response and validate score ranges.
     - Compute weighted final score.
     - Insert row into `info_ai_review`.
  4. Log successes/failures; skip or quarantine problematic articles without halting the batch.
- Include CLI arguments for batch size and dry-run mode (optional enhancement).
- Ensure the script can optionally sleep between requests based on an `AI_REQUEST_INTERVAL` variable to respect rate limits.

## 8. Writer Output Enhancements
- Update `manager/info_writer.py` so every article entry displays:
  - A prominently styled overall recommendation expressed as Chinese star ratings (`★` for each point) alongside the numeric weighted score.
  - A per-dimension breakdown showing 1–5 scores for the defined angles.
  - The one-sentence AI comment and AI summary, both rendered in Chinese beneath the headline for quick scanning.
- Ensure the HTML layout groups these elements in a readable card-like block, gracefully handling articles that still lack AI evaluations (show a clear placeholder message).

## 9. AI Client Implementation Notes
- Build a reusable `services/ai_client.py` (future task) that:
  - Reads configuration from environment variables.
  - Accepts prompt text and returns parsed JSON.
  - Handles HTTP errors, timeouts, retries, and JSON validation.
- Keep response schema validation strict—verify numeric ranges and required fields before writing to DB.

## 10. Error Handling & Monitoring
- Retry transient API failures with exponential backoff (configurable attempt count).
- On repeated failure, log the `info.id` and continue with the next row.
- Implement validation to ensure each score is an integer between 1 and 5; reject invalid payloads.
- Potentially log AI outputs to a debug table or file for auditing (optional future enhancement).

## 11. Security & Compliance
- Store secrets via environment variables managed by Conda (`environment.yml`); never log API keys.
- Redact article content in logs when unnecessary.
- Respect API usage policies and document request volumes.

## 12. Testing & Verification Plan
- Unit tests for:
  - Prompt rendering (ensure placeholders filled correctly).
  - AI client response parsing and validation.
  - Weighted average computation.
- Integration test stub simulating API responses via fixtures.
- Manual test procedure:
  1. Populate the Conda environment variables with test credentials or point the base URL to a mock server.
  2. Run `python manager/ai_evaluate.py --dry-run` to validate flow.
  3. Inspect database for new `info_ai_review` entries.

## 13. Rollout Considerations
- Backfill existing records via the manager script.
- Schedule periodic re-runs if article content changes or weights are updated.
- Communicate the new table schema to downstream consumers (dashboards, analytics).

