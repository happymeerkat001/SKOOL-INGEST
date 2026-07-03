# skool-ingest

Walk a Skool classroom, extract every embedded video URL, and fan them out to
[transcript.lol](https://transcript.lol) for URL-based transcription. Built
so you can extend any stage without re-architecting the others.

## Layout

    skool_ingest/
      transcript_lol.py   # thin REST client (submit, fetch, wait)
      manifest.py         # CSV-backed row model + IO
      skool_crawl.py      # classroom walker — SKELETON, you implement this
      fanout.py           # manifest → transcript.lol runner
      __main__.py         # CLI: crawl | fanout | status
    tests/
      test_smoke.py       # cred-free unit tests
    manifest/             # generated outputs (gitignored)
    cookies/              # your Skool cookies.txt (gitignored)
    captures/             # captured transcripts (gitignored)

## Quick start

    cd ~/Documents/Code/skool-ingest
    python3 -m venv .venv
    .venv/bin/pip install -e .[dev]

    # Optional: install Playwright (only needed for scripts/skool_login.py,
    # the interactive helper that produces cookies.txt for you).
    bash scripts/install_playwright.sh

    # Option A — interactive login helper (recommended; prompts for your
    # email + password, opens a browser, writes cookies.txt):
    .venv/bin/python scripts/skool_login.py

    # Option B — manual export:
    # Chrome: install "Get cookies.txt LOCALLY" extension, log into Skool,
    # export to ./cookies/skool.txt
    # Firefox: "cookies.txt" extension. Same idea.

    # Paste your transcript.lol API key
    cp .env.example .env
    $EDITOR .env

    # 3. Implement walk_classroom() in skool_ingest/skool_crawl.py
    #    See the docstring there for suggested backends (requests + cookies,
    #    Playwright, Notte). Skeleton raises NotImplementedError on purpose.

    # 4. Walk the classroom:
    .venv/bin/python -m skool_ingest crawl \
        --classroom-url "https://www.skool.com/<your-group>/classroom" \
        --cookies ./cookies/skool.txt \
        --out manifest/skool_videos.csv

    # 5. Fan out:
    .venv/bin/python -m skool_ingest fanout --manifest manifest/skool_videos.csv

    # 6. Check progress:
    .venv/bin/python -m skool_ingest status --manifest manifest/skool_videos.csv

## AI Lead Qualification Engine (`src/`)

Native engine built from `FB_Lead_Qualification_Architecture.md` (reverse-
engineered from the 20 archived masterclass transcripts). Deps isolated to
the `agent` extra: fastapi, uvicorn, httpx.

    src/
      main.py                # FastAPI: POST /webhook/fb-inbound, GET /healthz
      agent_core/
        templates.py         # gate template registry, COMPLIANCE_EXIT, target=333
        agents.py            # OccupancyEvaluator / ShowingCoordinator / IncomeVerifier
        router.py            # stage dispatch + Gemini personalization + outbound layer
    tests/test_agent_core.py # 26 tests

Design:
- **Deterministic-first**: binary gate failure (pets/kids/multi-occupant,
  income < 2.5x rent) returns COMPLIANCE_EXIT instantly — zero LLM tokens.
- **Gemini via httpx REST** (`GEMINI_API_KEY`, `GEMINI_MODEL` env). Unset key
  = template-only mode. Any HTTP error falls back to the raw template.
- **Speed-to-lead SLA**: per-request latency in body (`latency_ms`, `sla_met`
  < 20 s) and `X-Latency-MS` header; breach logs a warning.
- **Outbound layer** (`router.py`): OpenPhone Messages API
  (`https://api.openphone.com/v1/messages`, payload `{recipient, body, media?}`,
  `Authorization: $OPENPHONE_API_KEY`). Missing/blank key → prints
  "WARN: OPENPHONE_API_KEY unset. Operating in local simulation mode." and
  echoes the payload to console instead of raising. n8n callback posts the
  gate result to `N8N_WEBHOOK_URL` with `X-N8N-API-KEY: $N8N_API_KEY`.
- **Stateless**: n8n owns lead state; engine is pure per-request.

Run:

    .venv/bin/python -m pip install -e ".[agent,dev]"
    OPENPHONE_API_KEY=... GEMINI_API_KEY=... .venv/bin/python src/main.py
    curl -s localhost:8000/webhook/fb-inbound -X POST \
      -H 'content-type: application/json' \
      -d '{"lead_id":"t1","messages":[{"text":"me and my dog"}],
           "metadata":{"rent":800,"stage":1,"has_pets":true,"phone":"+15551234567"}}'

## FB lead triage harness (`fb_leads/`)

Local, read-only harness for turning operator-saved Facebook captures into a
triaged review queue. Version 1 does **not** log in, scrape live Facebook,
store credentials/cookies, send messages, post, rotate proxies, solve CAPTCHAs,
or bypass platform controls. A logged-in human saves pages/text/CSV exports they
are authorized to view, then the local pipeline parses and scores those files.

Layout:

    fb_leads/
      models.py      # LeadCandidate dataclass + JSONL store
      extract.py     # saved HTML/text/CSV extraction + sidecars
      ingest.py      # capture-dir walk + merge-preserving store update
      scoring.py     # deterministic explainable local scoring rules
      report.py      # review_queue.csv, review.html, CSV sync-back
      __main__.py    # CLI: ingest | score | report | sync | status | run

Manual capture procedure:

1. In your normal browser, log in to Facebook yourself.
2. Save visible/authorized Marketplace, group, or page material under
   `captures/fb/` using Save Page As, copy/paste to `.txt`, or CSV export.
3. Optional: add `<capture>.meta.json` beside a capture for source URL, source
   type, captured timestamp, or manual overrides when Facebook HTML is ugly.
4. Run the local dry-run pipeline. Outputs stay under gitignored
   `manifest/fb_leads/`.

Dry run against committed fixtures:

    .venv/bin/python -m fb_leads run \
        --captures tests/fixtures/fb_leads/captures \
        --out-dir /tmp/fb_leads_dryrun

Real local run:

    .venv/bin/python -m fb_leads run \
        --captures captures/fb \
        --out-dir manifest/fb_leads

Individual commands:

    .venv/bin/python -m fb_leads ingest --captures captures/fb --leads manifest/fb_leads/leads.jsonl
    .venv/bin/python -m fb_leads score --leads manifest/fb_leads/leads.jsonl
    .venv/bin/python -m fb_leads report --leads manifest/fb_leads/leads.jsonl --out-dir manifest/fb_leads
    .venv/bin/python -m fb_leads sync --csv manifest/fb_leads/review_queue.csv --leads manifest/fb_leads/leads.jsonl
    .venv/bin/python -m fb_leads status --leads manifest/fb_leads/leads.jsonl

Review workflow:
- `leads.jsonl` is the local source of truth.
- `review_queue.csv` is the editable operator queue; only `review_status` and
  `review_notes` sync back into JSONL.
- `review.html` is read-only for fast scanning. It uses local links back to raw
  captures and escapes lead text.

Compliance / safety stance:
- v1 is file-based and read-only: no live fetching from Facebook.
- `--live` on ingest refuses with a clear "not implemented in v1" message.
- No outreach or automated messaging exists in this harness.
- No credential/cookie storage, anti-detection, proxy rotation, CAPTCHA solving,
  or rate-limit evasion exists in `fb_leads/`.
- Facebook-derived lead data can contain personal display names; keep
  `captures/` and `manifest/fb_leads/` local/gitignored.

Future work, only after manual validation: browser-assisted capture behind
human approval, optional LLM scoring layered on deterministic rules, official
Graph API paths where authorized, and wiring approved leads into `src/agent_core`.

### Done / Next

Done:
- [x] 20/20 masterclass recordings transcribed locally + archived (iCloud + `r2:skool-archive`)
- [x] transcript.lol ingestion: 7/20 succeeded; 13 failed on their downloader, mp3 retry
      also rejected — blocked on R2 public dev URL toggle (see `TRANSCRIPT_LOL_R2_PLAYBOOK.md` §4)
- [x] Architecture blueprint extracted (`FB_Lead_Qualification_Architecture.md`)
- [x] Engine: gates, router, Gemini personalization, FastAPI webhook, SLA tracking
- [x] Outbound: OpenPhone payload/header contract + simulation mode, n8n callback
- [x] 111 tests green, ruff clean

Next:
- [ ] Enable R2 Public Development URL on skool-archive (Cloudflare dashboard, manual) →
      resubmit 13 failed recordings as pub-dev mp3 URLs + generate video index page
- [ ] Paste chatbot persona (playbook §3) into transcript.lol UI (manual, no API)
- [ ] Set real `OPENPHONE_API_KEY` / `N8N_WEBHOOK_URL` / `GEMINI_API_KEY` in `.env`,
      verify against OpenPhone live docs (field names + auth scheme) before first send
- [ ] Point self-hosted n8n FB-inbound workflow at `POST /webhook/fb-inbound`
- [ ] Deploy engine (uvicorn behind reverse proxy or launchd) + smoke test end-to-end
- [ ] Optional: configure git remote and push (repo currently local-only)

## Why "skeleton" for the crawler?

Because the right backend depends on what your Skool group actually looks
like. Some groups serve static HTML that a 30-line `requests + BeautifulSoup`
loop can scrape; others render client-side and need Playwright; some lock
down hard against headless browsers and need Notte. The skeleton defers that
choice to you so we don't bake the wrong one in.

## Tests

    .venv/bin/python -m pytest -q

The smoke tests are cred-free — they cover the manifest schema, the
embed-type detector, the cookies parser, and the transcript.lol client's
empty-key guard. Real end-to-end behavior is exercised by the CLI.

## Security

- `.env`, `cookies/`, and `manifest/*.json` are gitignored.
- The transcript.lol key and Skool cookies are read from environment /
  files at process start; they are never written to committed files.
- Per project rule, paste keys into `.env` yourself; this tool will not
  read them from the keychain on your behalf.
