# Release checklist (not executed: publication is a separate decision)

## Runtime files and history

- [x] `.gitignore` covers `data/*.sqlite*`, `data/*.log`, `data/serve.pid`, `data/cache/`, `web/node_modules`, `web/dist`, `web/test-results`, `demo/raw/*.webm`, `demo/out/*.mp4`, `.env`.
- [x] Runtime logs untracked (`git rm --cached data/*.log data/serve.pid`, commit `89643a6`).
- [ ] Before publishing: `git log --stat | grep -i "\.env\|key"` and a secrets scan of the full history (none expected: `.env` was never tracked; the Alchemy key is only in `.env`). If anything is found, rewrite history or start a fresh repository from a clean export.
- [ ] `data/probe.json` is tracked on purpose (measured source numbers used by `docs/COVERAGE.md`); confirm it holds no key (it does not: URLs are redacted by `env.redact`).
- [ ] Decide whether `demo/v2/raw/*.webm` and `demo/v2/out/*.mp4` ship with the repo (currently ignored) or as release assets.

## Configuration

- `.env.example` documents `ALCHEMY_KEY` (required for ingest/live/verify), `HYPERSYNC_TOKEN` (optional), `PUBLIC_RPC`, `STAMPEDE_DB`.
- Ports: API + web on 8791 (8787 is taken on this Mac); TUI connects with `--api-url`.
- Modes: `serve --mode fixture|replay|live [--speed]`; only `replay` exposes clock controls.

## Fixture / sample

- The recorded sample (PONS v2, 60 min, 2026-09-10 16:29:57–17:29:57 UTC) lives in `data/stampede.sqlite` (not tracked, 300+ MB with logs). For a public repo either: (a) ship `stampede export-fixture` JSON plus a smaller SQLite with trades/sequences only, or (b) document `ingest --minutes 60` so users record their own sample. Decide before publishing.
- `tests/fixtures/receipt_router_sell.json` is a real receipt (public chain data, no secrets).

## Fresh-install check (to run on a clean machine or a clean clone)

```sh
git clone <repo> stampede && cd stampede
cp .env.example .env            # fill ALCHEMY_KEY
uv sync && uv run pytest -q     # 41 passed expected (TUI tests need a TTY-less environment: fine)
cd web && npm install && npm run build && npm run lint && cd ..
python scripts/serve_daemon.py start --mode replay     # needs data/stampede.sqlite (see Fixture)
uv run stampede terminal --api-url http://127.0.0.1:8791
```

Known: `uv sync` installs Textual 8.2.8, FastAPI, requests; `npm install` pulls three 0.186, d3-force-3d, Playwright 1.63 (browsers via `npx playwright install chromium` if missing).

## Rollback

- Previous working version (Stage 1–3 web explorer, no TUI/3D): `git checkout v0.1-stage3`.
- The database format is forward-compatible: the new tables (`quotes`, `rejected_tokens`, indexes) are additive; `repair-ts` only fills unknown timestamps.

## Not part of this stage

Publishing the repository, a site, and the product posts remain separate owner decisions (see the brief, section 8).
