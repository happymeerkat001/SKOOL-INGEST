# skool-ingest — Refactor & Optimization Audit (2026-07-18)

> **STATUS: PLAN ONLY — NOTHING EXECUTED YET.**
> **Next step:** commit the in-flight work first (modified `README.md` + 3 untracked `docs/plans/*.md`), then hand this file to Codex: *"Execute REFACTOR-AUDIT-2026-07-18.md in priority order."*

## Verdict in one line

This is the best-run repo in the fleet — packaged (`pyproject.toml`), plan-doc discipline in `docs/plans/`, launchd agents documented — so the audit is short: land in-flight work, resolve the dual crawler stack, and double-check the secrets surface.

## P0 — Land the in-flight work

`README.md` modified + 3 plan docs untracked (July 6, 7, 15 — including `fix-resume-facebook-posting-plan`). The July-15 plan suggests FB posting is currently paused/broken; that plan is presumably the active priority, not this audit. Commit the docs, then either execute or explicitly shelve that plan.

## P1 — Secrets and state surface (verify, 10 min)

- `cookies/` and `captures/` at repo root hold authenticated session state. Verify both are gitignored (`git ls-files cookies captures` must be empty) and that nothing under `fb_leads/` output embeds tokens. If any hit, rotate + `git rm --cached` same day.
- `logs/` at root — gitignore if not already; launchd stdout/stderr grows unbounded otherwise.

## P2 — Dual crawler stack

`skool_ingest/skool_crawl.py` (497) and `skool_ingest/skool_crawl_notte.py` (255) are two implementations of the same job. Pick the live one, mark the other legacy in its docstring (or move to `archive/`), and record the decision in a plan doc. Two crawl paths = every future fix gets applied to the wrong one half the time.

## P3 — Opportunistic

- `scripts/local_ingest.py` (550) and `scripts/refresh_and_split.py` (248) overlap with the packaged `skool_ingest/` modules — fold shared logic into the package; scripts stay as thin CLIs.
- FB leads pipeline (`fb_leads/`, ~1.3k LOC across 5 files) is well-factored; add tests for `extract.py` parsing before it grows further.

## Explicitly do NOT

- Don't touch the n8n launchd environment hardening (recent commits eb0f3c2/a70cd10 — freshly stabilized).
- Don't restructure `docs/plans/` — the discipline is working; other repos should copy it.

## Codex order

1. Commit in-flight docs → decide on July-15 FB posting plan.
2. P1 secrets verification (report findings even if clean).
3. P2 crawler decision (needs one answer from Leon: which crawler is live?).
4. P3 opportunistically.
