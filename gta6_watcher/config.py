"""Settings, all from the environment so the deploying box owns the config.

Nothing here is a secret except the SMTP_* / MAIL_* values, which the deploying box
supplies through a root-only EnvironmentFile (never a committed file).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from . import github, portwatch, usage

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_LINES = 50
TOP_N = 5


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str
    claude_bin: str
    claude_timeout: int
    data_dir: Path
    always_email: bool
    max_lines: int = MAX_LINES
    portwatch_url: str = portwatch.DEFAULT_URL
    top_n: int = TOP_N
    github_api: str = github.DEFAULT_API
    github_token: Optional[str] = None
    fetch_timeout: int = 30
    claude_creds: Path = usage.DEFAULT_CREDS
    claude_usage_url: str = usage.DEFAULT_USAGE_URL

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        data_dir = Path(env.get("GTA6_WATCH_DATA_DIR") or REPO_ROOT / "data")
        return cls(
            model=env.get("GTA6_WATCH_MODEL") or DEFAULT_MODEL,
            claude_bin=env.get("GTA6_WATCH_CLAUDE_BIN") or "claude",
            claude_timeout=int(env.get("GTA6_WATCH_CLAUDE_TIMEOUT") or 600),
            data_dir=data_dir,
            always_email=_flag(env.get("GTA6_WATCH_ALWAYS_EMAIL")),
            portwatch_url=env.get("GTA6_WATCH_PORTWATCH_URL") or portwatch.DEFAULT_URL,
            top_n=int(env.get("GTA6_WATCH_TOP_N") or TOP_N),
            github_api=env.get("GTA6_WATCH_GITHUB_API") or github.DEFAULT_API,
            github_token=env.get("GTA6_WATCH_GITHUB_TOKEN") or None,
            fetch_timeout=int(env.get("GTA6_WATCH_FETCH_TIMEOUT") or 30),
            claude_creds=Path(env.get("GTA6_WATCH_CLAUDE_CREDS") or usage.DEFAULT_CREDS),
            claude_usage_url=env.get("GTA6_WATCH_CLAUDE_USAGE_URL") or usage.DEFAULT_USAGE_URL,
        )
