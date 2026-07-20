---
title: "feat: FB draft queue v2 — lead-driven draft suggestions"
type: feat
date: 2026-07-07
---

# feat: FB draft queue v2 — lead-driven draft suggestions

## Summary

Bridge the two shipped fb_leads systems: a `draft-suggest` command turns approved leads in `manifest/fb_leads/leads.jsonl` into prefilled, unapproved post drafts (template picked deterministically from lead tags), and the queue exports gain an approved-only view plus suggestion provenance. No posting, no sending, no LLM — operator approval stays the only path from draft to Meta Business Suite.

**Scope guard: stop at U3. Do not invent Phase 2** (no auto-posting, no LLM copy, no CRM, no scheduler daemon). Anything not in a U-ID below is out.

---

## Problem Frame

Draft creation is manual (`draft-add` per draft, operator types slots by hand) while the triage harness already holds approved leads with title/price/location extracted. The gap is one deterministic mapping: approved lead → prefilled draft candidate awaiting human review. Removing that friction raises nightly posting volume, which is what generates inbound traffic for the (separately gated) agent-core Stage B.

---

## Requirements

- R1. `draft-suggest` creates one draft per approved lead (`review_status == approved`) that has no existing draft referencing it via `lead_ids`; re-runs are idempotent (no duplicates).
- R2. Template selection is deterministic from lead tags: `coliving` tag → `coliving_room`, else `room_supply`/`owner`/`partnership` → `room_listing`; leads matching no mapped tag are skipped with a reason in the summary.
- R3. Suggested drafts prefill slots from the lead (`title`, `price_text`, `location`), record `lead_ids` and `template_id`, take the next free 2:30am slot, and are always `approved_by_human=no`.
- R4. Missing slot values fall back to a visible placeholder (e.g. `[FILL: location]`) in `copy_text` rather than failing the batch — the operator edits before approving.
- R5. `draft-export` gains `--only-approved` (queue view of paste-ready drafts); default export unchanged.
- R6. CSV/HTML/MD exports show provenance for suggested drafts: source lead id + lead title, template used.
- R7. `draft-list` summary reports suggested-pending-approval count separately.
- R8. Existing behavior preserved: manual `draft-add`, sync, checklist-field preservation, publish refusal stub — regression-proven by the existing suites.

---

## Key Technical Decisions

- **Suggestion = deterministic prefill, not generation:** local-only/no-paid-API constraint; also keeps every suggested draft's copy traceable to a fair-housing-reviewed template plus lead-extracted slots.
- **Dedupe by `lead_ids` linkage, not a new field:** the draft store already carries `lead_ids`; "lead has a draft" is derivable, so no schema change and no migration.
- **Placeholders over hard failure (R4):** batch suggestion over messy extracted leads must degrade per-draft; a `[FILL: …]` marker is operator-visible in the CSV and blocks nothing. Approval gate is the safety net, and R3 guarantees suggestions arrive unapproved.
- **No new subpackage:** suggest logic lands beside the existing draft code; one new module max, mirroring `fb_leads/scoring.py`'s table-driven style for the tag→template map.

---

## Implementation Units

### U1. `draft-suggest` command

- **Goal:** Approved leads become prefilled unapproved drafts, idempotently.
- **Requirements:** R1, R2, R3, R4
- **Dependencies:** none
- **Files:** `fb_leads/suggest.py` (new), `fb_leads/__main__.py`, `tests/test_fb_leads_suggest.py` (new)
- **Approach:** `suggest_drafts(leads_path, drafts_path) -> summary` loads both stores, filters approved leads without a linked draft, maps tags→template via a module-level table (first-match order: `coliving`, `room_supply`, `owner`, `partnership`), renders via `post_templates.render` with placeholder substitution for missing slots, assigns slots via existing `next_slot`, upserts via existing draft store. CLI subcommand prints JSON summary (`suggested`, `skipped_no_tag`, `skipped_existing`, `skipped_unapproved` counts) per `print_json` convention.
- **Patterns to follow:** `fb_leads/scoring.py` RULES table style; `fb_leads/drafts.py` store API; `cmd_*` CLI shape in `fb_leads/__main__.py`.
- **Test scenarios:**
  - Approved `coliving`-tagged lead → draft with `template_id=coliving_room`, slots prefilled, `approved_by_human=no`, `lead_ids=[lead.id]`.
  - Approved `room_supply` lead missing `location` → draft created with `[FILL: location]` in copy, batch continues.
  - Pending/rejected leads → skipped, counted.
  - Lead already linked from an existing draft → skipped; second `draft-suggest` run creates zero new drafts (R1 idempotence).
  - Lead with no mapped tag → skipped with `skipped_no_tag`.
  - Two suggestions in one run → consecutive 2:30am slots (existing auto-spacing holds).
  - Empty or missing leads file → clean zero summary, exit 0.
- **Verification:** run against the committed fb_leads fixtures pipeline output; summary counts match fixture expectations.

### U2. Export/provenance + approved-only view

- **Goal:** Operator sees where suggestions came from and can export only paste-ready drafts.
- **Requirements:** R5, R6, R7
- **Dependencies:** U1
- **Files:** `fb_leads/draft_report.py`, `fb_leads/__main__.py`, `tests/test_fb_leads_draft_report.py` (extend)
- **Approach:** Add `source_lead` display column (lead id + title resolved from leads store when `--leads` provided; blank for manual drafts) to CSV/HTML/MD; `--only-approved` filters `approved_by_human=yes` rows across all three artifacts; `draft-list` summary adds `suggested_pending` (drafts with non-empty `lead_ids` and `approved_by_human=no`). Sync contract untouched — provenance columns are display-only.
- **Test scenarios:**
  - Export with mixed manual+suggested drafts → suggested rows show lead id/title, manual rows blank.
  - `--only-approved` → only approved rows in CSV/HTML/MD; counts consistent across artifacts.
  - Sync after adding provenance columns still round-trips only the four checklist fields (R8 guard).
  - `draft-list` reports correct `suggested_pending` count.
- **Verification:** open HTML/MD, provenance visible; existing draft-report suite green.

### U3. Docs + operator checkpoints

- **Goal:** README documents the suggest→approve→schedule loop with operator-owned steps marked.
- **Requirements:** R8 (docs side)
- **Dependencies:** U1, U2
- **Files:** `README.md`
- **Approach:** Extend the posting-draft-queue section: nightly loop = `draft-suggest` → operator reviews CSV, fills `[FILL: …]`, approves → `draft-sync` → `draft-export --only-approved` → operator pastes into Meta Business Suite at the 2:30am slots `(manual)`. State plainly: suggestions never bypass approval; nothing posts or sends.
- **Test scenarios:** Test expectation: none — documentation only.
- **Verification:** cold read runs the loop end-to-end.

---

## Operator-owned checkpoints (not code, not this plan's units)

- Approve/reject suggested drafts in `post_queue.csv`; fill placeholders.
- Paste approved drafts into Meta Business Suite and schedule (2:30am cadence).
- Separately, when ready: Stage B runbook (OpenPhone key, allowlist, witnessed send) — untouched by this plan.

---

## Scope Boundaries

**Deferred until real live traffic / explicit ask:**

- Agent-core Stage B execution (operator runbook exists; zero code here).
- n8n lead-state machine — design from Stage B traffic.
- LLM copy variation or scoring assist.
- Daily digest / reporting script; deeper `leads.jsonl` integrity tooling.
- Any auto-posting or auto-sending — never without a human approval gate.

---

## Risks

- **FB ToS / fair housing:** unchanged exposure — posting stays manual via Meta's own tools; templates remain the single reviewed copy source. Do not add the playbook's furniture-category trick to templates (ToS gray zone stays out of the product).
- **Garbage-in from extraction:** `partial` leads produce placeholder-heavy drafts; approval gate + R4 visibility contain it. If placeholder rate is high, fix sidecars/extractors — not the suggester.
- **No new dependencies; no schema migration** — `lead_ids`/`template_id` already exist in the draft store.

---

## Sources & Research

- `fb_leads/drafts.py`, `fb_leads/post_templates.py` (`room_listing`, `coliving_room`), `fb_leads/scoring.py` (tags: `room_supply`, `owner`, `partnership`, `coliving`) — verified field/tag/template names this plan binds to.
- `docs/plans/2026-07-03-001-feat-fb-posting-draft-queue-plan.md` — parent; this is its "operator selects approved leads" loop made cheap.
