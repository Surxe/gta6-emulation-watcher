"""Cross-run state: when we last reported, which URLs we already sent, and the
per-port coverage cache used to frame "how has each port developed since we last
covered it".
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Keep the seen list bounded; the research prompt only needs recent history.
MAX_SEEN = 300


@dataclass
class State:
    last_success: Optional[str] = None  # ISO-8601 UTC
    seen_urls: List[str] = field(default_factory=list)
    # port id -> snapshot of what we last covered (name/repo/score/reached/summary/covered_at)
    ports: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: ignoring unreadable state {path}: {exc}")
            return cls()
        ports = raw.get("ports", {})
        if not isinstance(ports, dict):
            ports = {}
        return cls(
            last_success=raw.get("last_success"),
            seen_urls=[u for u in raw.get("seen_urls", []) if isinstance(u, str)],
            ports={k: v for k, v in ports.items() if isinstance(k, str) and isinstance(v, dict)},
        )

    def is_seen(self, url: str) -> bool:
        return _norm(url) in {_norm(u) for u in self.seen_urls}

    def port_snapshot(self, port_id: str) -> Optional[Dict[str, Any]]:
        return self.ports.get(port_id)

    def record(
        self,
        urls: Iterable[str],
        port_snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
        now: Optional[datetime] = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        self.last_success = now.replace(microsecond=0).isoformat()
        known = {_norm(u) for u in self.seen_urls}
        for url in urls:
            if _norm(url) not in known:
                self.seen_urls.append(url)
                known.add(_norm(url))
        self.seen_urls = self.seen_urls[-MAX_SEEN:]
        if port_snapshots:
            covered_at = now.date().isoformat()
            for port_id, snapshot in port_snapshots.items():
                self.ports[port_id] = {**snapshot, "covered_at": covered_at}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {
                "last_success": self.last_success,
                "seen_urls": self.seen_urls,
                "ports": self.ports,
            },
            indent=2,
        ) + "\n")
        os.replace(tmp, path)


def _norm(url: str) -> str:
    return url.strip().rstrip("/").lower()
