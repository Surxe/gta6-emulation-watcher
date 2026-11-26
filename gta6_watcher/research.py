"""Research stage: fetch PortWatch VI's top ports, look each one up on GitHub, then
run one agentic web search per port to find public developments since we last
covered it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import claude_cli, github, portwatch
from .config import PROMPTS_DIR, Settings
from .state import State
from .usage import UsageLedger

# First run (no state): look back this far.
DEFAULT_LOOKBACK_DAYS = 14
# Seen URLs passed to each port prompt (most recent first matters, the full list doesn't).
PROMPT_SEEN_LIMIT = 40

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
    port: str = ""
    port_name: str = ""
    repo: str = ""
    last_covered: str = ""
    last_score: float = 0.0
    last_reached: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        def text(key: str) -> str:
            return str(d.get(key) or "").strip()

        def num(key: str) -> float:
            value = d.get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            title=text("title"), url=text("url"), published=text("published"),
            source=text("source"), summary=text("summary"), status=text("status"),
            port=text("port"), port_name=text("port_name"), repo=text("repo"),
            last_covered=text("last_covered"), last_score=num("last_score"),
            last_reached=text("last_reached"),
        )

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


def gather(settings: Settings, state: State) -> List[portwatch.Port]:
    """Fetch the tracker, rank it, and enrich the top N ports with GitHub state.

    This is plain HTTP (no claude), so it is cheap and deterministic. A failure to
    reach PortWatch or GitHub is fatal: the digest is about these ports, so there is
    nothing sensible to report without them.
    """
    tracker = portwatch.fetch_tracker(settings.portwatch_url, settings.fetch_timeout)
    ports = portwatch.top_ports(tracker, settings.top_n)
    for port in ports:
        snapshot = state.port_snapshot(port.id) or {}
        port.last_covered = str(snapshot.get("covered_at") or "")
        port.last_score = float(snapshot.get("score") or 0.0)
        port.last_reached = str(snapshot.get("reached") or "")
        port.last_summary = str(snapshot.get("summary") or "")
        try:
            info = github.fetch_repo(
                port.repo, api=settings.github_api, token=settings.github_token,
                timeout=settings.fetch_timeout,
            )
        except github.GithubError as exc:
            print(f"research: github lookup for {port.repo} failed: {exc}")
            info = github.RepoInfo("", port.stars, port.forks, 0, port.pushed_at, False, "", "", "", "")
        github.apply_to_port(port, info)
    return ports


def _seen_block(state: State) -> str:
    seen = state.seen_urls[-PROMPT_SEEN_LIMIT:]
    return "\n".join(f"- {u}" for u in reversed(seen)) or "- (none yet)"


def _github_block(port: portwatch.Port) -> str:
    lines = [
        f"GitHub repo: {port.url} (stars {port.stars}, forks {port.forks}, "
        f"last push {port.pushed_at or 'unknown'})",
    ]
    if port.github_description:
        lines.append(f"Description: {port.github_description}")
    if port.github_license:
        lines.append(f"License: {port.github_license}")
    if port.github_archived:
        lines.append("The repo is ARCHIVED on GitHub.")
    if port.github_latest_release:
        lines.append(f"Latest release: {port.github_latest_release}")
    if port.github_readme_head:
        lines.append("README (head):\n" + port.github_readme_head)
    return "\n".join(lines)


def build_port_prompt(port: portwatch.Port, state: State, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    template = (PROMPTS_DIR / "research.md").read_text()
    since = port.last_covered or since_date(state, now)
    last_block = (
        f"Score {port.last_score}% ({port.last_reached}); summary: {port.last_summary}"
        if port.last_covered else "(never covered before by this watcher)"
    )
    highlights = "\n".join(f"- {h}" for h in port.highlights) or "- (none)"
    blockers = "\n".join(f"- {b}" for b in port.blockers) or "- (none)"
    return (
        template
        .replace("{today}", now.date().isoformat())
        .replace("{since}", since)
        .replace("{port_id}", port.id)
        .replace("{port_name}", port.name)
        .replace("{repo}", port.repo)
        .replace("{url}", port.url)
        .replace("{branch}", port.branch)
        .replace("{approach}", port.approach)
        .replace("{target}", port.target)
        .replace("{score}", str(port.score))
        .replace("{reached}", port.reached)
        .replace("{verification}", port.verification)
        .replace("{summary}", port.summary)
        .replace("{highlights}", highlights)
        .replace("{blockers}", blockers)
        .replace("{github}", _github_block(port))
        .replace("{last_covered}", port.last_covered or "never before")
        .replace("{last_snapshot}", last_block)
        .replace("{seen_urls}", _seen_block(state))
    )


def search_port(
    settings: Settings,
    state: State,
    port: portwatch.Port,
    ledger: Optional[UsageLedger] = None,
) -> List[Finding]:
    """One agentic web search for a single port, tagged with that port's identity."""
    result = claude_cli.run(
        build_port_prompt(port, state),
        model=settings.model,
        claude_bin=settings.claude_bin,
        timeout=settings.claude_timeout,
        allowed_tools=("WebSearch", "WebFetch"),
        json_schema=FINDINGS_SCHEMA,
        ledger=ledger,
    )
    findings: List[Finding] = []
    for d in result.get("items", []):
        if not isinstance(d, dict):
            continue
        f = Finding.from_dict(d)
        if not f.url:
            continue
        f = Finding(
            title=f.title, url=f.url, published=f.published, source=f.source,
            summary=f.summary, status=f.status, port=port.id, port_name=port.name,
            repo=port.repo, last_covered=port.last_covered,
            last_score=port.last_score, last_reached=port.last_reached,
        )
        findings.append(f)
    return findings


def search(
    settings: Settings,
    state: State,
    ports: List[portwatch.Port],
    ledger: Optional[UsageLedger] = None,
) -> List[Finding]:
    """Run one web search per port; drop anything already reported (belt and braces)."""
    findings: List[Finding] = []
    for port in ports:
        print(f"research: searching {port.name} ({port.repo}, {port.score}%)")
        findings.extend(search_port(settings, state, port, ledger))
    # Dedup across ports by URL, keeping first occurrence.
    seen: set = set()
    out: List[Finding] = []
    for f in findings:
        norm = f.url.strip().rstrip("/").lower()
        if norm in seen or state.is_seen(f.url):
            continue
        seen.add(norm)
        out.append(f)
    return out
