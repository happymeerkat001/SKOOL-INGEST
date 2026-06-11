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
