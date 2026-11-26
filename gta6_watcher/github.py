"""Fetch a GitHub repo's current state via the public REST API.

The research stage feeds this to the per-port web-search agent as a concrete
"where it stands now" anchor. No auth by default (60 req/hr is ample for 5 ports);
an optional ``GITHUB_TOKEN`` env var raises the limit.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

DEFAULT_API = "https://api.github.com"
README_CHARS = 1200


class GithubError(RuntimeError):
    """A repo lookup failed in a way we should surface."""


@dataclass(frozen=True)
class RepoInfo:
    description: str
    stars: int
    forks: int
    open_issues: int
    pushed_at: str
    archived: bool
    homepage: str
    license: str
    latest_release: str  # "tag · name · published" or ""
    readme_head: str
    missing: bool = False

    def to_dict(self) -> dict:
        return {
            "description": self.description, "stars": self.stars, "forks": self.forks,
            "open_issues": self.open_issues, "pushed_at": self.pushed_at,
            "archived": self.archived, "homepage": self.homepage,
            "license": self.license, "latest_release": self.latest_release,
            "readme_head": self.readme_head, "missing": self.missing,
        }


def _get(url: str, headers: dict, timeout: int):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def fetch_repo(
    repo: str,
    api: str = DEFAULT_API,
    token: Optional[str] = None,
    timeout: int = 20,
    readme_chars: int = README_CHARS,
) -> RepoInfo:
    """Return the repo's current metadata, latest release and a README head."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "gta6-emulation-watcher"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    status, body = _get(f"{api}/repos/{repo}", headers, timeout)
    if status == 404:
        return RepoInfo("", 0, 0, 0, "", False, "", "", "", "", missing=True)
    if status != 200:
        raise GithubError(f"GitHub repo lookup {repo} failed (HTTP {status})")
    try:
        meta = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GithubError(f"GitHub repo {repo} returned non-JSON: {exc}") from exc

    license_name = ""
    lic = meta.get("license")
    if isinstance(lic, dict):
        license_name = str(lic.get("spdx_id") or lic.get("name") or "")

    latest_release = ""
    rstatus, rbody = _get(f"{api}/repos/{repo}/releases/latest", headers, timeout)
    if rstatus == 200:
        try:
            rel = json.loads(rbody.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            rel = {}
        tag = rel.get("tag_name") or ""
        name = rel.get("name") or ""
        published = (rel.get("published_at") or "")[:10]
        if tag:
            latest_release = " · ".join(p for p in (tag, name, published) if p)

    readme_head = ""
    rstatus, rbody = _get(f"{api}/repos/{repo}/readme", {**headers, "Accept": "application/vnd.github.raw+json"}, timeout)
    if rstatus == 200:
        text = rbody.decode("utf-8", errors="replace").strip()
        readme_head = text[:readme_chars]

    return RepoInfo(
        description=str(meta.get("description") or ""),
        stars=int(meta.get("stargazers_count") or 0),
        forks=int(meta.get("forks_count") or 0),
        open_issues=int(meta.get("open_issues_count") or 0),
        pushed_at=str(meta.get("pushed_at") or ""),
        archived=bool(meta.get("archived")),
        homepage=str(meta.get("homepage") or ""),
        license=license_name,
        latest_release=latest_release,
        readme_head=readme_head,
    )


def apply_to_port(port, info: RepoInfo) -> None:
    """Copy a RepoInfo onto a Port object (mutates ``port``)."""
    port.github_description = info.description
    port.github_archived = info.archived
    port.github_homepage = info.homepage
    port.github_license = info.license
    port.github_latest_release = info.latest_release
    port.github_readme_head = info.readme_head
