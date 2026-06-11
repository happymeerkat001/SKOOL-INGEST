"""skool-ingest: walk Skool classrooms, hand video URLs to transcript.lol.

Public entry points live in submodules:

  - ``skool_ingest.transcript_lol``  – thin REST client for transcript.lol
  - ``skool_ingest.manifest``        – CSV-backed row model + IO helpers
  - ``skool_ingest.skool_crawl``     – classroom walker (skeleton; see NOTES)
  - ``skool_ingest.fanout``          – manifest → transcript.lol runner

Designed to be extended: every stage accepts its own dependency and writes
machine-readable artifacts, so swapping the crawler for a Notte/yt-dlp/etc.
backend is a single-file change.
"""
