"""Stage 2: a tool-less review agent condenses findings into <= N lines of markdown."""
from __future__ import annotations

import json
from datetime import date
from typing import Callable, List, Optional

from . import claude_cli
from .config import PROMPTS_DIR, Settings
from .research import Finding


def line_count(md: str) -> int:
    return len(md.strip("\n").splitlines())


def hard_trim(md: str, max_lines: int) -> str:
    """Last resort: keep the first max_lines lines, ending on a truncation note."""
    lines = md.strip("\n").splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[: max_lines - 1] + ["*(digest trimmed to fit)*"])


def strip_fence(md: str) -> str:
    """Models sometimes wrap the whole answer in ```markdown fences; unwrap it."""
    text = md.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def build_prompt(findings: List[Finding], max_lines: int, today: Optional[date] = None) -> str:
    template = (PROMPTS_DIR / "review.md").read_text()
    return (
        template.replace("{max_lines}", str(max_lines))
        .replace("{today}", (today or date.today()).isoformat())
        .replace("{findings}", json.dumps([f.to_dict() for f in findings], indent=2))
    )


def summarize(
    settings: Settings,
    findings: List[Finding],
    ask: Optional[Callable[[str], str]] = None,
) -> str:
    """Return the digest markdown, guaranteed to be <= settings.max_lines lines.

    ``ask`` is injectable for tests; by default it is a tool-less claude -p call.
    """
    if ask is None:
        def ask(prompt: str) -> str:
            return claude_cli.run(
                prompt,
                model=settings.model,
                claude_bin=settings.claude_bin,
                timeout=settings.claude_timeout,
            )

    limit = settings.max_lines
    md = strip_fence(ask(build_prompt(findings, limit)))
    if line_count(md) > limit:
        print(f"review: draft is {line_count(md)} lines (> {limit}); asking for a cut")
        md = strip_fence(ask(
            f"This markdown digest is {line_count(md)} lines. Rewrite it to at most "
            f"{limit} lines total, keeping the most important items and their links. "
            f"Output only the markdown.\n\n{md}"
        ))
    if line_count(md) > limit:
        print(f"review: still {line_count(md)} lines; hard-trimming to {limit}")
        md = hard_trim(md, limit)
    return md
