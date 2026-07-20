---
title: "fix: Resume Facebook posting and add staleness monitoring"
date: 2026-07-15
type: fix
status: planned
---

# fix: Resume Facebook posting and add staleness monitoring

## Summary

Facebook posts for the Assurance Relay page stopped publishing after 2026-06-24. Investigation this session established that publishing was never driven by this repo — it was driven by **ContentStudio** automation campaigns (feed-based curation from VentureBeat AI, Social Media Today, AI Marketing Feeds) connected to the page. The most likely stoppage cause is an expired Facebook access token, which per ContentStudio's docs disconnects the account and halts automation until reconnected. This plan (1) diagnoses and revives the ContentStudio pipeline, (2) adds a small in-repo staleness monitor so a future silent stoppage is detected within days instead of weeks, and (3) updates the README runbook so the recovery path is obvious.

The previously discussed in-repo replacement pipeline (n8n schedule + Meta Graph API capture + score-threshold auto-approve) is **deferred** — it only activates if ContentStudio turns out to be dead or unwanted. Its confirmed decisions are recorded under Scope Boundaries so they aren't lost.

---

## Problem Frame

- The operator believed posting was automatic and never manually intervened — correct: ContentStudio did it.
- Full git history confirms no posting automation ever existed in this repo; `fb_leads` is a separate, manual, lead-triage harness whose `draft-publish` is a refusal stub by design.
- The failure was silent for ~3 weeks because nothing watched the page for staleness.

Success criteria:
- Posts flow to the Facebook page again on ContentStudio's schedule.
- A silent stop is surfaced to the operator within a configurable number of days (default 3).
- A future "no content for many days" event has a documented, obvious runbook.

## Requirements

- **R1** — Diagnose why ContentStudio stopped publishing after 2026-06-24 and restore posting.
- **R2** — Detect future posting stoppage automatically: alert when the page's most recent post is older than N days (default 3).
- **R3** — Document the recovery runbook in `README.md`, with ContentStudio checks as step 0 (before the manual `fb_leads` fallback commands already added this session).

---

## Key Technical Decisions

- **Diagnosis-first, replacement deferred.** Reviving ContentStudio is likely a dashboard fix (token reconnect); building a replacement pipeline is days of work. (User-confirmed.)
- **Token expiry is the prime suspect.** ContentStudio disconnects an account and stops automation when the Facebook token expires — which happens after a password change or ~60 days without posting through the platform, and can also follow Facebook security checkpoints. Check Token Status first. (Source: ContentStudio Help Center, articles 498/669/688.)
- **Staleness monitor reads the page via Meta Graph API, read-only.** One-time setup of a long-lived Page access token; the monitor fetches the latest page post's timestamp daily. This keeps the repo's no-scraping stance intact (Graph API is the official surface) and works regardless of *which* tool publishes.
- **Alert channel: macOS user notification + non-zero exit logged by launchd**, mirroring the existing `com.leon.skool-ingest.*` launchd runner pattern. No new external dependencies.
- **Monitor lives in this repo** (`ops/monitoring/`) because launchd + dotenv + test conventions already exist here (`ops/n8n/README.md`, `tests/test_run_n8n_dotenv.py` pattern).

---

## Implementation Units

### U1. Diagnose and revive ContentStudio publishing

**Goal:** Posts resume on the Assurance Relay page.

**Requirements:** R1

**Dependencies:** none

**Files:** none (operational unit — findings recorded in the README runbook via U3)

**Approach:** Operator-driven checklist, in order of likelihood:
1. ContentStudio → Settings → Workspace Settings → Social Accounts → **Token Status** for the Assurance Relay Facebook page. If expired/disconnected: **Reconnect** (must be an Admin of the page; if the page requires 2FA, enable it on the account first).
2. Billing/subscription status — confirm the plan is active (a lapsed trial/plan also halts automation).
3. Automation → each campaign (VentureBeat AI, Social Media Today, AI Marketing Feeds) — confirm status is Active, and check the Notifications panel for "not enough content" warnings (fix by adding keywords/sources).
4. Planner → look for posts marked **Failed** since 2026-06-24; reschedule or let the campaign refill after reconnect.
5. Instagram and LinkedIn profiles were also connected ("Data Fetched" notices) — verify their tokens too while in the dashboard.

**Test scenarios:** Test expectation: none — operational diagnosis in a third-party dashboard, no repo code changes.

**Verification:** A campaign post publishes to the Facebook page (visible in Meta Business Suite / the page itself), and the Planner shows upcoming scheduled posts.

### U2. Facebook page staleness monitor

**Goal:** Automatic alert when the page hasn't posted in N days (default 3), regardless of which tool publishes.

**Requirements:** R2

**Dependencies:** U1 (needs the page active again to establish a healthy baseline; also produces the Page token setup)

**Files:**
- `ops/monitoring/fb_page_staleness.py` (new)
- `ops/monitoring/README.md` (new — token setup + launchd install steps)
- `ops/monitoring/com.leon.skool-ingest.fb-staleness.plist` (new template, installed to `~/Library/LaunchAgents/`)
- `tests/test_fb_page_staleness.py` (new)

**Approach:**
- Script reads `FB_PAGE_ID` and `FB_PAGE_TOKEN` from the repo dotenv (reuse the hardened dotenv-reading pattern locked down in `tests/test_run_n8n_dotenv.py`), calls Graph API `GET /{page-id}/posts?limit=1` (read-only), compares the newest post's `created_time` against a `--max-age-days` threshold (default 3).
- Fresh: exit 0 silently. Stale or API error (expired token, network): emit a macOS notification (`osascript display notification`) naming the condition and pointing at the README runbook, log details to stderr, exit non-zero.
- Daily launchd schedule via `StartCalendarInterval`, plist mirroring the existing `com.leon.skool-ingest.n8n` runner conventions (stdout/stderr log paths, working directory).
- Token setup (one-time, operator): Meta app → long-lived Page access token with `pages_read_engagement`; documented in `ops/monitoring/README.md`. An expired monitor token itself triggers the alert path, so monitoring fails loud, not silent.

**Patterns to follow:** launchd plist + dotenv conventions from the existing n8n runner (`ops/n8n/README.md`, `tests/test_run_n8n_dotenv.py`); stdlib-only HTTP if the repo convention avoids new deps, otherwise match existing HTTP client usage.

**Test scenarios:**
- Happy path: mocked API response with a post 1 day old, threshold 3 → exit 0, no notification invoked.
- Stale: newest post 5 days old, threshold 3 → non-zero exit, notification command invoked with a message containing the page name and age in days.
- Boundary: post age exactly equal to threshold → treated as fresh (document the chosen boundary in the script; test pins it).
- Empty feed: API returns zero posts → treated as stale (alert), not a crash.
- Error path: API returns 190 (expired token) → non-zero exit, notification says the *monitor token* needs refresh (distinct message from stale-page).
- Error path: network timeout → non-zero exit, alert fires (fail-loud).
- Config: missing `FB_PAGE_TOKEN` in dotenv → clear error message, non-zero exit.

**Verification:** Running the script against the live page after U1 exits 0; temporarily setting `--max-age-days 0` produces a visible macOS notification; `launchctl list` shows the job loaded and its log file receives a line after the scheduled run.

### U3. README recovery runbook update

**Goal:** The "no posts for days" runbook starts with ContentStudio, then the monitor, then the manual `fb_leads` fallback.

**Requirements:** R3

**Dependencies:** U1 (verified checklist), U2 (monitor exists to reference)

**Files:** `README.md`

**Approach:** Extend the quick-recovery block added 2026-07-15 (top of the FB leads section): step 0 = ContentStudio Token Status / billing / campaign checks (the U1 checklist, condensed); step 1 = check the staleness monitor's log; existing manual `fb_leads` command sequence remains as the fallback content path. Clarify that ContentStudio owns automated publishing and `fb_leads` is the separate manual lead-post path.

**Test scenarios:** Test expectation: none — documentation-only change.

**Verification:** README reads as a single ordered runbook; a reader hitting "no posts for many days" can resolve it without this plan document.

---

## Scope Boundaries

### Out of scope
- Browser-automation scraping of Facebook (reverses the repo's explicit compliance stance; ToS risk).
- Auto-publishing from this repo (user chose to stop at exported drafts; ContentStudio owns publishing).
- Changes to the n8n agent-core Stage A inbound workflow.

### Deferred to Follow-Up Work
- **In-repo replacement pipeline** — only if ContentStudio proves dead or unwanted. Confirmed decisions to carry into that plan: scheduled n8n workflow drives the pipeline; capture via official Meta Graph API (no scraping); rule/score-based auto-approve using the existing `fb_leads/scoring.py` threshold, guarded by a daily post cap, an audit log of every auto-approval, and a kill switch; pipeline stops at `draft-export --only-approved` output (manual paste remains).
- Extending the staleness monitor to the Instagram/LinkedIn profiles ContentStudio also manages.

---

## Risks & Dependencies

- **ContentStudio account may be lapsed/deleted** — if U1 finds no recoverable account, the deferred replacement pipeline gets promoted to active work (return to this plan).
- **Graph API token setup friction (U2)** — requires a Meta developer app and page admin access; if blocked, an interim fallback is alerting off ContentStudio's own notification emails, but that couples the monitor to one vendor.
- **Prohibited-action boundary:** reconnecting the Facebook account in ContentStudio is an auth/permissions action the operator must perform themselves; the agent can't do OAuth reconnects on the user's behalf.

---

## Sources & Research

- ContentStudio Help Center: [Facebook post failed to publish](https://docs.contentstudio.io/article/498-facebook-post-failed-to-publish), [refresh an expired token](https://docs.contentstudio.io/article/669-how-to-refresh-token-expiry), [Facebook errors while publishing](https://docs.contentstudio.io/article/688-facebook-errors) — token expiry disconnects the account and stops automation; reconnect under Settings → Social Accounts → Token Status.
- Session investigation (2026-07-15): full git history shows no posting automation ever existed in this repo; single n8n workflow is inbound-only; `manifest/fb_leads/` never created locally; launchd jobs healthy and unrelated to posting.
