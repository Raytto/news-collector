# Repository Guidelines

## Project Structure & Module Organization
The scrapers that power this project live in `demo/scraping/`, one script per publication (for example `gamedeveloper.com.latest.py`). Keep shared helpers at the top of each module, prefer pure functions so they can be reused, and document source-specific notes inline. Specs or runbooks belong in `docs/demo/`, while dependency pins sit in `requirements.txt`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — create and enter an isolated environment.
- `pip install -r requirements.txt` — install scraping dependencies (`requests`, `beautifulsoup4`, `feedparser`, `sgmllib3k`).
- `python demo/scraping/<script>.py` — run a scraper directly; prefer `python -m demo.scraping.<module>` if you add packages.
- `PYTHONPATH=. python -m compileall demo` — quick sanity check that modules import cleanly.

## Coding Style & Naming Conventions
Follow standard PEP 8 with 4-space indents and type hints on every public function (existing modules already use `from __future__ import annotations`). Module names stick to lowercase plus dots that mirror the source (e.g., `gameindustry.biz.rss`). Functions should be verbs (`fetch_homepage`, `extract_articles`), constants stay uppercase snake case, and temporary selectors or CSS classes belong in expressive variables.

## Testing Guidelines
New scrapers should ship with lightweight parsing tests under `demo/scraping/tests/` that mock network calls and feed stored HTML or RSS samples. Use `pytest` (install it locally even though it is not pinned yet) and name files `test_<script>.py` so discovery works. Target at least one regression test per parser branch plus a freshness filter test, and execute `pytest demo/scraping/tests -q` before opening a pull request.

## Commit & Pull Request Guidelines
The distributed snapshot lacks full Git history, but upstream commits follow concise imperative subjects (`feat: add naavik digest scraper`) with optional bodies describing rationale and links. Keep bodies wrapped at 72 chars, reference issues with `Refs #123`, and prefer one scraper or feature per change. Pull requests should include a summary, manual run logs (e.g., sample command output), updated docs if behavior shifts, and screenshots or pasted snippets that prove the scraper still finds fresh articles.

## Security & Configuration Tips
Never hard-code API keys or cookies; read secrets from environment variables and document them in `docs/` if needed. Limit outbound requests by honoring each site’s robots.txt and throttle retries to avoid blocks. When sharing logs, redact URLs that include personal tokens and reuse the provided user-agent template as a baseline for new clients.
