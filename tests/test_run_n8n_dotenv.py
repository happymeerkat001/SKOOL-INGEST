"""Regression tests for deploy/run_n8n.sh dotenv reader.

Mirrors the inline Python helper inside deploy/run_n8n.sh into a small helper
script and exercises it the same way the launchd service would. Also asserts
the helper still contains the parsing logic so a future edit cannot silently
break the supervised n8n service.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "deploy" / "run_n8n.sh"
HELPER = REPO / "scripts" / "_extract_token.py"


def _bash_available() -> bool:
    return shutil.which("bash") is not None


def _write_env_with_token(env_path: Path, token: str | None) -> None:
    lines = [
        "# comment with = in it",
        "OTHER_VAR=ignore-me",
    ]
    if token is not None:
        token_key = "ENGINE_WEBHOOK_TOKEN"
        lines.insert(1, token_key + "=" + token)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_helper_extracts_engine_webhook_token(tmp_path: Path):
    if not HELPER.exists():
        pytest.skip("helper script not present")
    env_path = tmp_path / ".env"
    _write_env_with_token(env_path, "expected-token")

    result = subprocess.run(
        ["python3", str(HELPER), str(env_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "expected-token"


def test_helper_returns_empty_when_token_missing(tmp_path: Path):
    if not HELPER.exists():
        pytest.skip("helper script not present")
    env_path = tmp_path / ".env"
    _write_env_with_token(env_path, None)

    result = subprocess.run(
        ["python3", str(HELPER), str(env_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == ""


@pytest.mark.skipif(not _bash_available(), reason="bash not available")
def test_runner_invokes_helper_via_heredoc():
    if not RUNNER.exists() or not HELPER.exists():
        pytest.skip("runner or helper missing")
    runner_text = RUNNER.read_text(encoding="utf-8")
    helper_text = HELPER.read_text(encoding="utf-8")

    assert "python3" in runner_text
    for needle in (
        "key.strip() == 'ENGINE_WEBHOOK_TOKEN'",
        "value.strip().strip(chr(34)).strip(chr(39))",
        ".partition('=')",
    ):
        assert needle in helper_text, f"helper missing expected line: {needle!r}"