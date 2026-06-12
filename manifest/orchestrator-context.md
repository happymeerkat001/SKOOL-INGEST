# Orchestrator + Skool Ingest Context for Claude

This section gives Claude the engineering context for the Skool -> transcript.lol -> Obsidian pipeline. Read this once. You don't need to memorize it. Use it to answer questions like "how does the pipeline work", "what's broken", "how do I re-run the failed downloads", and "what would a 3-line code change do".

## What this system is

Two repos working together:

1. `/Users/leon/Documents/Code/Obsidian-vault-orchestrator/`
   - Generic Transcriber -> Obsidian vault tool
   - Originally built for YouTube / Vimeo / Transcript.lol
   - Has a Chrome extension bridge, a local HTTP server, a daily-note linker
   - Used to ingest one URL at a time or to bulk-export from a Transcript.lol space

2. `/Users/leon/Documents/Code/skool-ingest/`
   - Skool-specific crawler + submitter
   - Walks a Skool classroom, pulls each post's `__NEXT_DATA__`, extracts the video URL, classifies by `embed_type`
   - Submits everything to Transcript.lol; falls back to local download + faster-whisper for protected streams

Together they form a "Skool classroom -> markdown note in Obsidian" pipeline.

## Repository map (orchestrator)

```
Obsidian-vault-orchestrator/
  cli/
    transcribe.py              - Submit one URL to Transcript.lol, get transcript
    export_transcripts.py     - Pull all completed recordings from a space, render to vault
    transcript.py             - Save a single media URL into the vault
    transcript_server.py      - Local HTTP server (port 8765) for the Chrome extension
    transcript_lol_summary.py - Use Transcript.lol prompts to summarize a transcript
    media_captions.py         - YouTube + Vimeo caption fallback (no API needed)
    archive_youtube.py        - Bulk: ingest root-level notes containing YouTube URLs
    daily_note_youtube.py     - Bulk: ingest Daily Notes/YYYY-MM-DD.md
    scrape_notes.py           - Bulk: ingest iPhone-shared date notes
    reprocess_youtube_stubs.py - Bulk: turn old bare-URL notes into real transcripts
    hermes_worker.py          - Generic Hermes job worker (unrelated, don't touch)
    hermes_to_kanban.py       - Kanban integration (unrelated)
    hermes_kanban_server.py   - Kanban server (unrelated)
    transfer_learning_to_neural.py - Cross-vault keyword transfer (out of scope)
    google_reauth.py          - Re-authorize Google OAuth2 for related services
  chrome-extension/            - Posts YouTube URLs from any tab to local server
  ingest/                     - Hedy AI -> Obsidian sync (separate feature)
  process/                    - OCR + Imgur post-processing (separate feature)
  scripts/                    - Misc utility scripts
  tests/                      - Unit tests
  CLAUDE.md                   - This project's Claude instructions
  README.md                   - Human-readable docs
```

## Repository map (skool-ingest)

```
skool-ingest/
  skool_ingest/
    __init__.py
    __main__.py               - python3 -m skool_ingest <command>
    skool_crawl.py            - Walk a Skool classroom, extract videos
    skool_crawl_notte.py      - Same but driven by Notte (cloud browser)
    skool_login.py            - Cookie harvest helpers
    transcript_lol.py         - Submit a row to transcript.lol
    manifest.py               - CSV manifest schema (13 columns)
    vault.py                  - Render notes into the Obsidian vault
    fanout.py                 - Submit many rows in parallel
  scripts/
    skool_playwright_crawl.py - Re-crawl with a logged-in Playwright session
    refresh_and_split.py      - Refresh manifest, split into per-embed-type CSVs
    build_claude_bundle.py    - Build the consolidated bundle Claude reads
    local_ingest.py           - **New**: download + transcribe protected streams locally
    probe_cookie.py           - Inspect Skool cookies file
    install_playwright.sh     - Install Playwright + Chromium
  manifest/
    skool_videos.csv          - 24 rows, all current sessions
    transcripts/              - Raw transcript.lol text dumps
    local_ingest/             - Workdir for downloaded mp3s + JSON + summary
  tests/
    test_crawl_parsing.py     - Skool HTML parsing
    test_local_ingest.py      - **New**: local_ingest unit tests
  cookies/
    skool.txt                 - Netscape cookie file
  .env                        - All credentials (read by python-dotenv, never sourced)
  .env.example                - Template
  run.sh                      - Top-level entry point
```

## Environment variables (read by python-dotenv, never `source`d)

```
Transcript.lol_Login            - email
Transcript.lol_Password         - password
TRANSCRIPT_LOL_SUMMARY_PROMPT_ID - prompt id for summaries
TRANSCRIPT_LOL_API_KEY          - optional, if user has one (Leon does NOT)
SKOOL_COOKIES_PATH              - path to cookies/skool.txt
SKOOL_Username                  - Skool login (used by skool_login.py)
SKOOL_Password                  - Skool password (used by skool_login.py)
NOTTE_API_KEY                   - optional, for cloud-browser crawling
SKOOL_CLASSROOM_URL             - which classroom to crawl
VAULT_DIR                       - default vault root
VAULT_OUTPUT_DIR                - alternate output dir
TRANSCRIPT_LOL_TIER             - cosmetic; the user is on Free Lifetime Tier 4
```

Authorization priority in `TranscriptClient.authenticate()`:
1. `TRANSCRIPT_LOL_API_KEY` -> x-api-key header
2. `TRANSCRIPT_LOL_AUTH_TOKEN` -> Authorization header
3. `TRANSCRIPT_LOL_SESSION_COOKIE` -> Cookie header
4. `Transcript.lol_Login` + `Transcript.lol_Password` + `FIREBASE_API_KEY` -> Firebase bearer
5. `Transcript.lol_Login` + `Transcript.lol_Password` (no FIREBASE_API_KEY) -> Playwright browser fallback (added today)

The user is currently on path 5 (browser fallback). FIREBASE_API_KEY is intentionally unset.

## Pipeline state right now (2026-06-11)

What's working
- Skool classroom crawl -> 24 rows in `manifest/skool_videos.csv`
  - 4 YouTube rows (public URLs)
  - 20 m3u8 rows (protected Skool CDN, tokenized)
- Transcript.lol auth via Playwright browser login
- Transcript.lol export of the 4 YouTube items -> 4 vault notes in `AI-Vault/Skool Ingest/`
  - 4 numbered stub notes (underwriting-calculators, creative-finance, about-me, who-is-andres)
  - 4 unnumbered full-text notes (rendered via export_transcripts.py)
- Obsidian-vault-orchestrator extended to support `TRANSCRIPT_LOL_SPACE_NAME` (workspace-by-name resolution)
- Obsidian-vault-orchestrator extended with Playwright browser-login fallback
- 10 unit tests in Obsidian-vault-orchestrator (3 new for space-name resolution + env loading)
- 10 unit tests in skool-ingest (new for local_ingest)

What's broken
- 20 m3u8 transcript.lol submissions: all failed at transcript.lol's import worker with "Unexpected error while downloading video! Please try again later."
- local_ingest.py: 20/20 m3u8 downloads failed in the last run
  - 17 with "No such file or directory" (the `audio/` subdir wasn't being created) -> **fixed**, code now `audio_path.parent.mkdir(parents=True, exist_ok=True)`
  - 3 with "End of file" / "Broken pipe" (m3u8 token expired mid-download) -> **not yet fixed**
- m3u8 token expiry: the Skool stream tokens live ~minutes-to-an-hour; the manifest captures them at crawl time, so a re-crawl is needed right before download

Coverage of the bundle right now
- 6 real transcripts (4 YouTube, 2 from transcript.lol)
- 4 stubs (Skool post captured but transcript not available)
- 14 sessions with no notes in the vault at all
- The bundle honestly says "STUB" on the 4 stubs; the 14 missing ones are simply not in the bundle

## How to re-run things (for the user's own use)

```
1. Refresh the manifest (requires Skool cookies; ~3 min)
cd ~/Documents/Code/skool-ingest
PYTHONPATH=. .venv/bin/python scripts/skool_playwright_crawl.py

2. Submit 4 YouTube items to transcript.lol
.venv/bin/python -m skool_ingest run

3. Download + transcribe 20 m3u8 items locally
.venv/bin/python scripts/local_ingest.py \
  --vault-dir "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault/Skool Ingest"

4. Re-build the bundle (do this after step 3 lands more transcripts)
.venv/bin/python scripts/build_claude_bundle.py

5. Bulk-export completed transcript.lol recordings into the vault
cd ~/Documents/Code/Obsidian-vault-orchestrator
python3 cli/export_transcripts.py \
  --output-dir "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault/Skool Ingest"
```

## Key design decisions (so Claude can answer "why is it this way")

- **Why CSV manifest and not JSON?** Excel triage. The user opens it manually. Header is self-documenting.
- **Why no API key path?** The user does not have a Transcript.lol API key and doesn't want one. The login + Playwright path was the original design; the API key path is just an early-exit optimization.
- **Why local_ingest.py separate from transcript_lol.py?** transcript_lol.py assumes the URL is reachable from transcript.lol's import worker. For protected streams that assumption is false. Local_ingest downloads to disk, transcribes with faster-whisper, writes to vault directly. The two paths don't share state.
- **Why faster-whisper tiny.en?** M2 Air, 16 GB RAM, time. tiny.en is ~75 MB, int8 quantization, ~5x real-time on M2. Good enough for a coaching call where signal density is low. Easy to swap to `base.en` or `small.en` with `--model`.
- **Why Python-dotenv instead of `source .env`?** The `.env` has keys with dots in the name (`Transcript.lol_Login`). `source` parses dots as part of variable names; dotenv doesn't. Also safer — no shell-injection surface.
- **Why Playwright (not requests) for the browser login?** Transcript.lol's login form uses dynamic React state machine. Plain requests with a hardcoded payload is fragile. Playwright drives a real browser session and exports the resulting cookies. Slow but correct.

## What Claude should NOT do in this project

- Don't suggest creating a Transcript.lol API key as the "right" answer. The user explicitly does not want one. The browser fallback is the desired path.
- Don't suggest Notte / cloud browser as the "right" answer for Skool crawling. It's a fallback for when the user has no local Skool cookies. The user has local cookies.
- Don't suggest moving transcripts.lol fetching to a different service (AssemblyAI, Deepgram). The user is on free tier; costs would land on him.
- Don't suggest GitHub Actions / cron / scheduled runs unless asked. Pipeline runs on demand.
- Don't suggest dockerizing. M2 Mac, native run, no need.

## What's worth doing next (in priority order, for the user to choose from)

1. Fix m3u8 token expiry in local_ingest.py:
   - re-crawl immediately before download
   - use shorter timeouts, retry on stream cut
   - small per-file work unit so partial progress is preserved
2. Switch faster-whisper from `tiny.en` to `base.en` for better accuracy (~2x slower, ~150 MB)
3. Add a coverage map to the bundle so Claude can quantify "X% covered"
4. After 1+2, re-run pipeline; bundle grows from 6 real to ~20 real
5. Add Skool->Obsidian auto-naming (use post title as vault note title, not URL hash)
