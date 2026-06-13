"""Generate video index page (HTML) and vault note (Markdown) for 20 R2-hosted recordings."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

R2_BASE = "https://pub-cb322ef18aa04b85b06eaaa7158ab209.r2.dev"

TITLES = [
    "1db3cd0991c990ba-Coliving Construction Mastermind 0917",
    "1ff00db434a87385-AI-Native Marketing Strategies & Management 0429",
    "279479ac313ff0f9-FB Marketing everything you'll ever need to know",
    "29c4d2c2443275f4-Tenant Acquisition The Sales Process 0111",
    "31a92eb4a1ece559-Appraisals & Refinance Coliving + 911 Subto Audit",
    "3302415c2b08cc7f-Underwriting 140 door Portfolio in Texas 0408",
    "4884e769b7c2d7b9-Systems to Operate like a Coliving Pro 0513",
    "4da14fb50f56292a-FB Marketing Ads Review & Accounting 0204",
    "69d06c2519df2814-Be a good Landlord AC control & Maintenance 0422",
    "69e15f91f9492fe6-Philadelphia Market & AI Automations 0211",
    "6eadf3bc1d2cd3a6-FB Marketplace-AI Software-Operational SOPs 0401",
    "7f18ee5b5b9fff13-Private Money Partnerships Dos and Dont's 0128",
    "874d1c64af51ac1c-AI Automations Introduction - 0225",
    "876ef610a3acb28d-Houston Property",
    "947ce9935022ac9a-FB Marketplace Live Audit & Business Mindset 0304",
    "a278021d741021b1-Padsplit Underwriting Deal or No Deal 1022",
    "a41b56dc9763a830-Underwriting, I bought this house 0924",
    "c7c62ecbb1f0f24f-Q & A 031820206",
    "f1596dcf79c963d9-My SOPs tenant screening & onboarding 1015",
    "fd60d70f584d0b30-Live Underwriting & Market Analysis [How to] 0415",
]


def enc(filename: str) -> str:
    return urllib.parse.quote(filename, safe="")


def display_name(title: str) -> str:
    # Strip the hex prefix (first 16 + dash)
    parts = title.split("-", 1)
    return parts[1] if len(parts) > 1 else title


def build_html(out: Path) -> None:
    rows = []
    for t in TITLES:
        name = display_name(t)
        vid = f"{R2_BASE}/video/loops/{enc(t + '.mp4')}"
        aud = f"{R2_BASE}/audio/{enc(t + '.mp3')}"
        rows.append(
            f'<tr><td>{name}</td>'
            f'<td><a href="{vid}" target="_blank">mp4</a></td>'
            f'<td><a href="{aud}" target="_blank">mp3</a></td></tr>'
        )
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Coliving Masterclass — Video Index</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #ddd; }}
  a {{ color: #0066cc; }}
</style></head><body>
<h1>Coliving Masterclass — Video Index</h1>
<p>{len(TITLES)} recordings from <code>r2:skool-archive</code>.
Click to stream directly from R2 (no download required).</p>
<table>
<tr><th>Title</th><th>Video</th><th>Audio</th></tr>
{''.join(rows)}
</table>
</body></html>"""
    out.write_text(html)
    print(f"wrote {out} ({len(TITLES)} rows)")


def build_vault_note(out: Path) -> None:
    lines = [
        "---",
        "title: Coliving Masterclass Video Index",
        "source: r2:skool-archive via pub-dev URL",
        "date: 2026-06-13",
        "tags: [skool-ingest, video, index]",
        "---",
        "",
        "# Coliving Masterclass Video Index",
        "",
        f"{len(TITLES)} recordings. Click to stream from R2.",
        "",
        "| # | Title | Video | Audio | Transcript |",
        "|---|-------|-------|-------|------------|",
    ]
    for i, t in enumerate(TITLES, 1):
        name = display_name(t)
        vid = f"{R2_BASE}/video/loops/{enc(t + '.mp4')}"
        aud = f"{R2_BASE}/audio/{enc(t + '.mp3')}"
        note = f"[[{t}]]"
        lines.append(f"| {i} | {name} | [mp4]({vid}) | [mp3]({aud}) | {note} |")
    lines.append("")
    out.write_text("\n".join(lines))
    print(f"wrote {out} ({len(TITLES)} rows)")


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[1]
    build_html(repo / "manifest" / "video_index.html")

    vault = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/AI-Vault/Skool Ingest"
    if vault.is_dir():
        build_vault_note(vault / "Video Index.md")
    else:
        print(f"vault dir not found: {vault}")
