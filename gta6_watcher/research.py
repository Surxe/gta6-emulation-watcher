"""Stage 1: an agentic web-search pass that returns structured findings."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import claude_cli
from .config import PROMPTS_DIR, Settings
from .state import State

# First run (no state): look back this far.
DEFAULT_LOOKBACK_DAYS = 14
# Seen URLs passed to the prompt (most recent first matters, the full list doesn't).
PROMPT_SEEN_LIMIT = 80

FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "published": {"type": "string", "description": "YYYY-MM-DD or empty if unknown"},
                    "source": {"type": "string"},
                    "summary": {"type": "string"},
                    "status": {"type": "string", "enum": ["confirmed", "claim", "rumor"]},
                },
                "required": ["title", "url", "published", "source", "summary", "status"],
            },
        }
    },
    "required": ["items"],
}


@dataclass(frozen=True)
class Finding:
    title: str
    url: str
    published: str
    source: str
    summary: str
    status: str

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(**{k: str(d.get(k) or "").strip() for k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return asdict(self)


def since_date(state: State, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if state.last_success:
        try:
            return datetime.fromisoformat(state.last_success).date().isoformat()
        except ValueError:
            pass
    return (now - timedelta(days=DEFAULT_LOOKBACK_DAYS)).date().isoformat()


def build_prompt(state: State, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    seen = state.seen_urls[-PROMPT_SEEN_LIMIT:]
    seen_block = "\n".join(f"- {u}" for u in reversed(seen)) or "- (none yet)"
    template = (PROMPTS_DIR / "research.md").read_text()
    return (
        template.replace("{today}", now.date().isoformat())
        .replace("{since}", since_date(state, now))
        .replace("{seen_urls}", seen_block)
    )


def search(settings: Settings, state: State) -> List[Finding]:
    """Run the search agent; drop anything already reported (belt and braces)."""
    result = claude_cli.run(
        build_prompt(state),
        model=settings.model,
        claude_bin=settings.claude_bin,
        timeout=settings.claude_timeout,
        allowed_tools=("WebSearch", "WebFetch"),
        json_schema=FINDINGS_SCHEMA,
    )
    findings = [Finding.from_dict(d) for d in result.get("items", []) if isinstance(d, dict)]
    return [f for f in findings if f.url and not state.is_seen(f.url)]
