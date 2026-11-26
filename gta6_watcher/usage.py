"""Track claude CLI usage across a run so the digest can report it.

Claude Code on this box runs on a subscription (a flat monthly plan), so there is
no per-call invoice to sum. What we can observe is:

- per-call token counts / wall time / (when the CLI reports one) a cost estimate,
  summed across every ``claude -p`` call in a run (``UsageLedger``), and
- the plan's 5-hour and weekly usage windows as percentages, read from Claude's
  OAuth usage endpoint before and after the run (``fetch_plan_usage``).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

DEFAULT_CREDS = Path.home() / ".claude" / ".credentials.json"
DEFAULT_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def _num(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _usd(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PlanUsage:
    """The plan's usage windows as percentages (0-100), as reported by the OAuth
    usage endpoint. ``None`` means the value was unavailable."""
    five_hour_pct: Optional[float]
    weekly_pct: Optional[float]
    five_hour_resets_at: Optional[str] = None
    weekly_resets_at: Optional[str] = None


@dataclass
class UsageLedger:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: Optional[float] = None
    duration_ms: int = 0

    def add(self, envelope: Mapping[str, Any]) -> None:
        """Accumulate one ``claude -p`` JSON envelope's usage fields."""
        self.calls += 1
        usage = envelope.get("usage")
        if isinstance(usage, Mapping):
            self.input_tokens += _num(usage.get("input_tokens") or usage.get("inputTokens"))
            self.output_tokens += _num(usage.get("output_tokens") or usage.get("outputTokens"))
            self.cache_read_tokens += _num(
                usage.get("cache_read_input_tokens") or usage.get("cacheReadInputTokens")
            )
            self.cache_write_tokens += _num(
                usage.get("cache_creation_input_tokens") or usage.get("cacheCreationInputTokens")
            )
        # Cost shows up under several names across CLI versions; take the first found.
        for key in ("total_cost_usd", "cumulative_cost_usd", "cost_usd", "costUsd", "totalCostUSD"):
            value = _usd(envelope.get(key))
            if value is not None:
                self.cost_usd = value if self.cost_usd is None else self.cost_usd + value
                break
        self.duration_ms += _num(envelope.get("duration_ms") or envelope.get("duration_api_ms"))

    def render_footer(
        self,
        before: Optional[PlanUsage] = None,
        after: Optional[PlanUsage] = None,
    ) -> str:
        """A short markdown footer summarizing run usage and plan-window deltas."""
        lines: list = []
        if self.calls:
            tokens = f"{self.input_tokens:,} input / {self.output_tokens:,} output tokens"
            parts = [f"{self.calls} claude call(s)", tokens]
            if self.cache_read_tokens:
                parts.append(f"{self.cache_read_tokens:,} cached-read tokens")
            if self.duration_ms:
                parts.append(f"{self.duration_ms / 1000:.0f}s wall time")
            if self.cost_usd is not None:
                parts.append(f"~${self.cost_usd:.4f} estimated")
            lines.append("**Claude usage (this run):** " + ", ".join(parts) + ".")

        if before is not None or after is not None:
            lines.append(_plan_line(before, after))

        if not lines:
            return ""
        lines.append(
            "*On the Claude subscription (flat monthly plan) — not billed per call, "
            "but the usage above counts toward the plan's limits.*"
        )
        return "---\n" + "\n".join(lines) + "\n"


def _fmt_pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def _plan_line(before: Optional[PlanUsage], after: Optional[PlanUsage]) -> str:
    f5_b = _fmt_pct(before.five_hour_pct if before else None)
    f5_a = _fmt_pct(after.five_hour_pct if after else None)
    wk_b = _fmt_pct(before.weekly_pct if before else None)
    wk_a = _fmt_pct(after.weekly_pct if after else None)
    text = f"**Plan usage:** 5h window {f5_b} → {f5_a} · weekly {wk_b} → {wk_a}"
    resets = (after.weekly_resets_at if after else None) or (before.weekly_resets_at if before else None)
    if resets:
        text += f" (weekly resets {resets[:10]})"
    return text + "."


def fetch_plan_usage(
    creds_path: Optional[Path] = None,
    url: str = DEFAULT_USAGE_URL,
    timeout: int = 15,
) -> Optional[PlanUsage]:
    """Read the plan's 5-hour and weekly usage windows from Claude's OAuth endpoint.

    Never raises: any failure (no credentials, stale token, network, parse) returns
    ``None`` so a digest still goes out with "n/a" for the windows.
    """
    path = Path(creds_path) if creds_path else DEFAULT_CREDS
    try:
        creds = json.loads(path.read_text())
        token = creds.get("claudeAiOauth", {}).get("accessToken")
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return None
    if not token:
        return None

    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "User-Agent": "gta6-emulation-watcher"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    five = data.get("five_hour") or {}
    seven = data.get("seven_day") or {}
    return PlanUsage(
        five_hour_pct=_pct(five.get("utilization")),
        weekly_pct=_pct(seven.get("utilization")),
        five_hour_resets_at=five.get("resets_at"),
        weekly_resets_at=seven.get("resets_at"),
    )
