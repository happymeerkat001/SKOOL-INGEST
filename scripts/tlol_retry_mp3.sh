#!/bin/bash
# Resubmit the 13 failed transcript.lol recordings as mp3 presigned URLs.
# Success = COMPLETED status string in CLI output, NOT exit code.
set -u
LOG="manifest/local_ingest/tlol-retry-mp3.log"
CLI="$HOME/Documents/Code/Obsidian-vault-orchestrator/cli/transcribe.py"

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

submit() {
  local title="$1"
  local url
  url=$(rclone link "r2:skool-archive/audio/${title}.mp3" --expire 168h) || return 1
  python3 "$CLI" "$url" --title "$title (audio)" --timeout 900 2>&1
}

ok=0; fail=0
for title in "${TITLES[@]}"; do
  echo "=== SUBMIT: $title ===" >> "$LOG"
  out=$(submit "$title")
  echo "$out" | tail -5 >> "$LOG"
  if echo "$out" | grep -qiE "COMPLETED|TRANSCRIPTION_COMPLETE|\"status\": ?\"(READY|DONE|SUCCEEDED)\""; then
    echo "=== OK : $title ===" >> "$LOG"; ok=$((ok+1))
  else
    echo "--- retry with fresh presign: $title ---" >> "$LOG"
    out=$(submit "$title")
    echo "$out" | tail -5 >> "$LOG"
    if echo "$out" | grep -qiE "COMPLETED|TRANSCRIPTION_COMPLETE"; then
      echo "=== OK (retry) : $title ===" >> "$LOG"; ok=$((ok+1))
    else
      echo "=== FAILED : $title ===" >> "$LOG"; fail=$((fail+1))
    fi
  fi
  sleep 3
done
echo "=== SUMMARY: ok=$ok fail=$fail of ${#TITLES[@]} ===" >> "$LOG"
echo "ok=$ok fail=$fail"
