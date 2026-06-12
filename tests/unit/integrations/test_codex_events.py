"""Unit tests for Codex raw-event helpers."""

from __future__ import annotations

from pathlib import Path

from agentic_company.integrations.codex.events import extract_codex_usage


def test_extract_codex_usage_takes_last_cumulative(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"type": "turn.started"}\n'
        '{"usage": {"cached_input_tokens": 100, "input_tokens": 500, "output_tokens": 20}}\n'
        '{"usage": {"input_tokens": 800, "output_tokens": 35}}\n',
        encoding="utf-8",
    )

    assert extract_codex_usage(path) == (800, 35)


def test_extract_codex_usage_handles_flat_fields(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"input_tokens": 42, "output_tokens": 7}\n', encoding="utf-8")

    assert extract_codex_usage(path) == (42, 7)


def test_extract_codex_usage_missing_file_or_usage(tmp_path: Path):
    assert extract_codex_usage(tmp_path / "absent.jsonl") == (None, None)

    no_usage = tmp_path / "events.jsonl"
    no_usage.write_text('{"type": "thread.started"}\n', encoding="utf-8")
    assert extract_codex_usage(no_usage) == (None, None)
