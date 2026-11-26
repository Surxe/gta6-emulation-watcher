"""Thin wrapper around the local `claude -p` CLI (the box's subscription login).

One function, one place that knows the CLI's flags and its JSON envelope, so the
research and review stages stay free of subprocess details.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Optional, Sequence

from .usage import UsageLedger


class ClaudeError(RuntimeError):
    """The CLI failed, timed out, or returned something unusable."""


def run(
    prompt: str,
    *,
    model: str,
    claude_bin: str = "claude",
    timeout: int = 600,
    allowed_tools: Sequence[str] = (),
    json_schema: Optional[dict] = None,
    ledger: Optional[UsageLedger] = None,
) -> Any:
    """Run one non-interactive Claude turn and return its result.

    Returns the parsed structured output (a dict) when ``json_schema`` is given,
    otherwise the final text. The prompt goes in on stdin so its length is not
    bounded by argv. When ``ledger`` is given, this call's usage fields from the
    CLI's JSON envelope are accumulated into it.
    """
    cmd = [claude_bin, "-p", "--model", model, "--output-format", "json"]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema)]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise ClaudeError(f"claude CLI not found ({claude_bin}); is it on PATH?") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"claude CLI timed out after {timeout}s") from exc

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise ClaudeError(f"claude CLI exited {proc.returncode}: {tail}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"claude CLI returned non-JSON output: {proc.stdout[:300]!r}") from exc
    if envelope.get("is_error"):
        raise ClaudeError(f"claude reported an error: {envelope.get('result')!r}")

    if ledger is not None:
        ledger.add(envelope)

    if json_schema is None:
        text = envelope.get("result")
        if not isinstance(text, str) or not text.strip():
            raise ClaudeError("claude returned an empty result")
        return text

    structured = envelope.get("structured_output")
    if structured is None:
        # Fall back to a JSON body in the text result (older CLI shapes).
        try:
            structured = json.loads(_strip_fence(envelope.get("result") or ""))
        except json.JSONDecodeError as exc:
            raise ClaudeError("claude returned no structured output") from exc
    if not isinstance(structured, dict):
        raise ClaudeError(f"structured output is not an object: {structured!r}")
    return structured


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
