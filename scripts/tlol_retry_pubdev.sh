#!/bin/bash
# Resubmit 13 failed transcript.lol recordings using R2 public dev URL (mp3).
# Public URLs need no signature — HEAD works, no expiry.
set -u
LOG="manifest/local_ingest/tlol-retry-pubdev.log"
CLI="$HOME/Documents/Code/Obsidian-vault-orchestrator/cli/transcribe.py"
BASE="https://pub-cb322ef18aa04b85b06eaaa7158ab209.r2.dev/audio"

TITLES=(
"3302415c2b08cc7f-Underwriting 140 door Portfolio in Texas 0408"
"4884e769b7c2d7b9-Systems to Operate like a Coliving Pro 0513"
"69d06c2519df2814-Be a good Landlord AC control & Maintenance 0422"
"69e15f91f9492fe6-Philadelphia Market & AI Automations 0211"
"6eadf3bc1d2cd3a6-FB Marketplace-AI Software-Operational SOPs 0401"
"7f18ee5b5b9fff13-Private Money Partnerships Dos and Dont's 0128"
"874d1c64af51ac1c-AI Automations Introduction - 0225"
"947ce9935022ac9a-FB Marketplace Live Audit & Business Mindset 0304"
"a278021d741021b1-Padsplit Underwriting Deal or No Deal 1022"
"a41b56dc9763a830-Underwriting, I bought this house 0924"
"c7c62ecbb1f0f24f-Q & A 031820206"
"f1596dcf79c963d9-My SOPs tenant screening & onboarding 1015"
"fd60d70f584d0b30-Live Underwriting & Market Analysis [How to] 0415"
)

url_encode() {
  python3 -c "import urllib.parse; print(urllib.parse.quote('$1', safe=''))"
}

ok=0; fail=0
for title in "${TITLES[@]}"; do
  encoded=$(url_encode "${title}.mp3")
  url="${BASE}/${encoded}"
  echo "=== SUBMIT: $title ===" >> "$LOG"
  echo "  url=$url" >> "$LOG"
  out=$(python3 "$CLI" "$url" --title "$title" --timeout 900 2>&1)
  echo "$out" | tail -8 >> "$LOG"
  if echo "$out" | grep -qiE "COMPLETED|TRANSCRIPTION_COMPLETE|READY|DONE|SUCCEEDED"; then
    echo "=== OK : $title ===" >> "$LOG"; ok=$((ok+1))
  else
    echo "=== FAILED : $title ===" >> "$LOG"; fail=$((fail+1))
  fi
  sleep 3
done
echo "=== SUMMARY: ok=$ok fail=$fail of ${#TITLES[@]} ===" >> "$LOG"
echo "ok=$ok fail=$fail"
