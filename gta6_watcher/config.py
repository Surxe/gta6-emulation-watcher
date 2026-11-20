"""Settings, all from the environment so the deploying box owns the config.

Nothing here is a secret except the SMTP_* / MAIL_* values, which the deploying box
supplies through a root-only EnvironmentFile (never a committed file).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_LINES = 50


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
        )
