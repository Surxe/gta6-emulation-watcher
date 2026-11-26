"""Fetch and score PortWatch VI's public tracker (https://portwatch-vi.xyz).

The site publishes ``data/tracker.json`` (refreshed ~every 3 hours). The "race"
leaderboard is computed client-side from that data, not stored in it, so we
replicate the scoring model from the site's ``assets/js/scoring.js`` to rank the
efforts ourselves and pick the top N by progress.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Tuple

DEFAULT_URL = "https://portwatch-vi.xyz/data/tracker.json"

# Scoring model copied from portwatch-vi.xyz/assets/js/scoring.js (dependency-free
# there; kept verbatim so it stays in sync). 0-100 = "how close is this to playing
# GTA VI on PC, start to finish". Groundwork fills the first 10%, then a ladder of
# milestones to Playable (50%), then four side-by-side tracks fill 50-100%.
MILESTONES = {
    "groundwork": 0, "loads": 10, "boots": 20, "menu": 30, "world": 40,
    "playable": 50, "complete": 100,
}
GROUNDWORK = (("loader", 0.2), ("shaders", 0.28), ("graphics", 0.32), ("proven", 0.2))
TRACKS = (("story", 0.35), ("render", 0.25), ("perf", 0.2), ("stability", 0.2))
STABILITY = {
    "unknown": 0.0, "constant": 0.0, "often": 0.25, "sometimes": 0.55,
    "rarely": 0.8, "stable": 1.0,
}
LADDER = ("loads", "boots", "menu", "world", "playable")


class PortwatchError(RuntimeError):
    """The tracker could not be fetched or parsed."""


@dataclass
class Port:
    """One ranked effort from the race, plus its GitHub state and coverage cache."""
    id: str
    name: str
    repo: str
    branch: str
    approach: str
    target: str
    score: float
    reached: str
    summary: str
    highlights: List[str]
    blockers: List[str]
    verification: str
    stars: int
    forks: int
    pushed_at: str
    gta: Mapping[str, Any]
    # Filled in by github.py:
    github_description: str = ""
    github_archived: bool = False
    github_homepage: str = ""
    github_license: str = ""
    github_latest_release: str = ""
    github_readme_head: str = ""
    # Coverage cache (from state): when we last covered it and what we said.
    last_covered: str = ""
    last_score: float = 0.0
    last_reached: str = ""
    last_summary: str = ""

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    def snapshot(self) -> dict:
        """The fields to cache as "what we last covered" for the next run."""
        return {
            "name": self.name,
            "repo": self.repo,
            "score": self.score,
            "reached": self.reached,
            "summary": self.summary,
        }

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "name": self.name, "repo": self.repo, "branch": self.branch,
            "approach": self.approach, "target": self.target, "score": self.score,
            "reached": self.reached, "summary": self.summary,
            "highlights": self.highlights, "blockers": self.blockers,
            "verification": self.verification, "stars": self.stars,
            "forks": self.forks, "pushed_at": self.pushed_at, "gta": dict(self.gta),
            "github_description": self.github_description,
            "github_archived": self.github_archived,
            "github_homepage": self.github_homepage,
            "github_license": self.github_license,
            "github_latest_release": self.github_latest_release,
            "github_readme_head": self.github_readme_head,
            "last_covered": self.last_covered, "last_score": self.last_score,
            "last_reached": self.last_reached, "last_summary": self.last_summary,
        }
        return d


def _clamp01(v: Any) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, v))


def _ladder_value(v: Any) -> float:
    if v is True:
        return 1.0
    if v is False or v is None:
        return 0.0
    return _clamp01(v)


def _groundwork_fraction(gw: Optional[Mapping[str, Any]]) -> float:
    if not gw:
        return 0.0
    return sum(w * _clamp01(gw.get(k)) for k, w in GROUNDWORK)


def _track_values(gta: Optional[Mapping[str, Any]]) -> dict:
    gta = gta or {}
    stability = gta.get("stability")
    if isinstance(stability, (int, float)):
        stability = _clamp01(stability)
    else:
        stability = STABILITY.get(stability, STABILITY["unknown"])
    return {
        "story": _clamp01(gta.get("story")),
        "render": _clamp01(gta.get("render")),
        "perf": _clamp01((float(gta.get("fps") or 0.0)) / 30.0),
        "stability": stability,
    }


def score_effort(effort: Mapping[str, Any]) -> Tuple[float, str]:
    """Return ``(score, reached)`` for one effort, mirroring scoring.js."""
    gw = _groundwork_fraction(effort.get("groundwork"))
    gta = effort.get("gta") or {}
    score = MILESTONES["loads"] * gw
    reached = "groundwork"
    prev_tick = score
    for key in LADDER:
        v = _ladder_value(gta.get(key))
        if v >= 1.0:
            score = float(MILESTONES[key])
            prev_tick = score
            reached = key
            continue
        if v > 0.0:
            score = max(score, prev_tick + v * (MILESTONES[key] - prev_tick))
        break

    tracks = _track_values(gta)
    quality = sum(w * tracks[k] for k, w in TRACKS)
    if reached == "playable":
        score = MILESTONES["playable"] + quality * (MILESTONES["complete"] - MILESTONES["playable"])
        if quality >= 1.0:
            reached = "complete"

    manual = effort.get("manualScore")
    if isinstance(manual, (int, float)):
        score = min(100.0, max(0.0, float(manual)))

    return round(score * 10) / 10, reached


def _is_private(effort: Mapping[str, Any]) -> bool:
    repo = effort.get("repo")
    return not (isinstance(repo, str) and "/" in repo)


def _stars(effort: Mapping[str, Any]) -> int:
    return int((effort.get("stats") or {}).get("stars") or 0)


def ranked_efforts(tracker: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """The race: tracked, public, not nuked efforts, best score first."""
    efforts = [
        e for e in tracker.get("efforts", [])
        if e.get("tier") == "tracked" and not e.get("nuked") and not _is_private(e)
    ]
    efforts.sort(key=lambda e: (-score_effort(e)[0], -_stars(e)))
    return efforts


def fetch_tracker(url: str = DEFAULT_URL, timeout: int = 30) -> dict:
    """Download and parse the tracker; raises PortwatchError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "gta6-emulation-watcher"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (OSError, ValueError) as exc:
        raise PortwatchError(f"could not fetch PortWatch tracker {url}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortwatchError(f"PortWatch tracker {url} was not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or "efforts" not in data:
        raise PortwatchError(f"PortWatch tracker {url} is missing the efforts list")
    return data


def top_ports(tracker: Mapping[str, Any], n: int = 5) -> List[Port]:
    """Return the top ``n`` race efforts as Port objects (best progress first)."""
    ports: List[Port] = []
    for effort in ranked_efforts(tracker):
        if len(ports) >= n:
            break
        score, reached = score_effort(effort)
        stats = effort.get("stats") or {}
        ports.append(Port(
            id=str(effort.get("id") or ""),
            name=str(effort.get("name") or ""),
            repo=str(effort.get("repo") or ""),
            branch=str(effort.get("branch") or "main"),
            approach=str(effort.get("approach") or ""),
            target=str(effort.get("target") or ""),
            score=score,
            reached=reached,
            summary=str(effort.get("summary") or ""),
            highlights=[str(h) for h in (effort.get("highlights") or [])],
            blockers=[str(b) for b in (effort.get("blockers") or [])],
            verification=str(effort.get("verification") or ""),
            stars=int(stats.get("stars") or 0),
            forks=int(stats.get("forks") or 0),
            pushed_at=str(stats.get("pushedAt") or ""),
            gta=dict(effort.get("gta") or {}),
        ))
    return ports
