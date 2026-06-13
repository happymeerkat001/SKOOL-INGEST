# Execution Playbook: R2 → Transcript.lol Ingestion

Goal: batch-import the 20 processed coliving masterclass recordings from the
`skool-archive` R2 bucket into transcript.lol (AI viewing, playback, chatbot)
with zero local disk overhead — transcript.lol streams directly from R2.

## 1. Data Transfer Strategy

Submit **video (mp4)** from `video/loops/` — user needs in-app playback, so
transcript.lol must ingest the visual stream, not just audio. (~500 MB/item;
transcript.lol fetches server-side from R2, zero local disk.) The mp3s in
`audio/` remain the lightweight archival/transcription fallback.

Steps (all idempotent, state in `manifest/r2_fanout_state.json`):

1. **Presign** each object: `rclone link "r2:skool-archive/audio/<file>.mp3" --expire 168h`
   - 168h = max practical window; transcript.lol fetches once at submit time,
     so even minutes of validity suffice. 7 days gives retry headroom.
2. **Verify** one link with a ranged GET (`curl -r 0-1023`) — expect 206.
   - NOTE: `curl -I` (HEAD) returns 403 by design; presigned signature covers GET only.
3. **Authenticate**: `cli/transcribe.py --test-auth` in Obsidian-vault-orchestrator
   (Firebase bearer; space 678568d76d74d77ee0ef382c).
4. **Pilot**: submit 1 file end-to-end, confirm COMPLETED status + transcript text.
5. **Batch**: remaining 19, sequential with 2-5 s spacing (matches existing
   fanout etiquette in `skool_ingest fanout --sleep 2.0`). Record
   `recording_id` per file.
6. **Poll** every cron fire (10 min) until all terminal
   (COMPLETED/READY vs FAILED). Retry failures once with a fresh presigned URL.

Failure boundaries: per-file. A failed submit/poll never blocks the rest.

## 2. Endpoint Formatting — R2 URL Schema

Transcript.lol's "Import from URL" needs a plain HTTPS URL it can GET anonymously.
Three valid schemas, in order of preference:

### A. Presigned S3 URL (in use — no bucket exposure, no Cloudflare config)
```
https://<ACCOUNT_HASH_ID>.r2.cloudflarestorage.com/skool-archive/audio/<FILE>.mp3
  ?X-Amz-Algorithm=AWS4-HMAC-SHA256
  &X-Amz-Credential=<ACCESS_KEY>%2F<DATE>%2Fauto%2Fs3%2Faws4_request
  &X-Amz-Date=<ISO8601>
  &X-Amz-Expires=604800            # 168h max
  &X-Amz-SignedHeaders=host
  &X-Amz-Signature=<SIG>
```
Generate: `rclone link "r2:skool-archive/audio/<FILE>.mp3" --expire 168h`
Spaces in filenames are percent-encoded automatically.

### B. Public Development URL (requires enabling in R2 dashboard)
```
https://pub-<PUBLIC_BUCKET_HASH>.r2.dev/audio/<FILE>.mp3
https://pub-<PUBLIC_BUCKET_HASH>.r2.dev/video/loops/<FILE>.mp4
```
Rate-limited by Cloudflare; fine for one-shot imports. Makes the whole bucket public — avoid unless presign fails.

### C. Custom Domain (requires DNS + R2 custom-domain binding)
```
https://media.<YOUR_DOMAIN>/audio/<FILE>.mp3
https://media.<YOUR_DOMAIN>/video/loops/<FILE>.mp4
```
Cacheable, permanent, supports access rules. Overkill for 20 files.

Local disk overhead in all three: zero — bytes flow R2 → transcript.lol directly.

## 3. System Persona Blueprint — chatbot prompt template

Paste into transcript.lol custom chatbot config post-ingest:

```
ROLE
You are LEASING-FUNNEL-ANALYST, a domain-locked extraction engine for a
coliving rental operation. You analyze ONLY the ingested masterclass
recordings. Refuse all questions outside high-velocity leasing funnel
mechanics with: "Out of scope: I only analyze the leasing funnel corpus."

SCOPE (exhaustive — nothing else)
1. THREE_MESSAGE_ELIMINATION_MATRIX
   - MSG_1_GATE  := occupancy constraints (single-adult), pet exclusion
   - MSG_2_GATE  := location/commute fit, move-in timeline
   - MSG_3_GATE  := showing commitment
   - INCOME_GATE := 2.5x rent minimum (post-showing application)
   For each: extract verbatim script lines, disqualification triggers,
   and pass-through rates when stated.
2. AGENT_ROUTER_TOPOLOGY
   - Enumerate the 15-16 single-mission agents and the router pattern.
   - For each agent: AGENT_NAME, MISSION, INPUT_SOURCE, OUTPUT_TARGET,
     LLM_BACKEND (Gemini/ChatGPT), FAILURE_MODE if mentioned.
3. MICRO_LEASING_SOPS
   - Cross-reference operational SOPs applicable to PadSplit-style
     room-by-room leasing: posting cadence, showing windows, onboarding
     protocol, escalation/manual-fallback rules.

OUTPUT CONTRACT (strict)
- Every claim carries a citation: [RECORDING_TITLE @ HH:MM:SS].
- Use variable-label format:
    VARIABLE_NAME: <value>
    EVIDENCE: "<short verbatim quote>"
    SOURCE: [title @ timestamp]
- Quantitative claims (response-time SLA, message volumes, conversion
  rates) must quote the number verbatim; never round or infer.
- If the corpus does not contain an answer, output:
  NOT_IN_CORPUS: <topic> — do not speculate, do not use outside knowledge.
- Conflicting statements across recordings: list both with citations and
  flag CONFLICT.

STYLE
Terse. Tables for enumerations. No preamble, no summaries unless asked.
```

## 4. Status (corrected 2026-06-12)

- [x] Presigned URL schema verified (206 ranged GET)
- [x] transcript.lol auth verified (Firebase bearer)
- [x] Pilot: Coliving Construction Mastermind 0917 mp4 — COMPLETED, diarized transcript returned
- [ ] ~~Batch: 19/19 mp4s COMPLETED~~ **WRONG — earlier claim trusted shell exit codes.**
      Reality (log: manifest/local_ingest/tlol-batch.log): **13/20 recordings FAILED**
      server-side ("Download failed! ... use the file upload option instead"),
      ~30-50 s after submit on the ~500 MB looped mp4s. Only 7 completed.
- [x] mp3 retry attempted (manifest/local_ingest/tlol-retry-mp3.log):
      **0/13 — MEDIA_IMPORT_FAILED within ~8 s** even on ~25 MB mp3s.
      URL imports from presigned `*.r2.cloudflarestorage.com` are effectively dead
      (instant rejection; possibly HEAD preflight vs GET-only signature, or their
      fetcher blocked). Not fixable by retrying.
- [x] Finding: transcript.lol converts file imports to **audio-only** playback —
      in-app video viewing is not achievable via URL import. Video playback moves
      to R2 directly (public dev URL + index page).
- [x] R2 Public Development URL enabled: `pub-cb322ef18aa04b85b06eaaa7158ab209.r2.dev`
      HEAD returns 200, Content-Type correct, full download works.
- [x] Retry with public dev mp3 URLs: **still MEDIA_IMPORT_FAILED** (0/13).
      transcript.lol's importer rejects these files regardless of URL format.
      (log: manifest/local_ingest/tlol-retry-pubdev.log; earlier script falsely
      reported ok=13 — grep matched "Firebase sign-in succeeded" not recording status.)
- [x] Video index built: `manifest/video_index.html` + vault `Video Index.md` —
      20 permanent clickable R2 pub-dev links for mp4/mp3 browser streaming.
- [ ] Manual file upload: drag 13 mp3s from iCloud SkoolArchive/audio/ into
      transcript.lol UI (only remaining path; ~5 min task for human)
- [ ] Chatbot persona injected (manual UI step — config UI has no API; prompt in §3)

Note: no upload API exists in either client (URL-submit only); UI file upload uses
a native file picker, not automatable. Local whisper transcripts for all 20 already
exist in the vault as fallback corpus.

## 5. Wall-clock

Presign+submit: ~2 s/file. transcript.lol processing: ~3-10 min per ~1 h audio,
runs server-side in parallel. Expect all 20 terminal within ~30-60 min of batch
submit. Polling automated every 10 min.
