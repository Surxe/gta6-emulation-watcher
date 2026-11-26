"""Entry point: portwatch -> per-port search -> review -> render -> email.

Stages:
  0. Fetch PortWatch VI's tracker, rank it, and look the top N ports up on GitHub
     (plain HTTP, no claude).
  1. One agentic web search per port, framed as "what changed since we last covered
     it" (cache: state.ports).
  2. Review (tool-less claude) -> markdown, hard-capped at MAX_LINES.
  3. Append a claude-usage footer, render markdown -> HTML, email.

Exit codes: 0 = sent, skipped (nothing new), or email not configured;
            1 = a fetch or claude stage failed; 2 = SMTP delivery failed.
"""
from __future__ import annotations

import argparse
import json
import smtplib
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import mailer, portwatch, render, research, review, usage
from .claude_cli import ClaudeError
from .config import Settings
from .research import Finding
from .state import State
from .usage import UsageLedger


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gta6_watcher", description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="run the full pipeline, write outputs to the data dir, "
                        "print the markdown; never email and never update state")
    p.add_argument("--findings", type=Path, metavar="FILE",
                   help="skip the fetch/search stages and review these findings (JSON list)")
    return p.parse_args(argv)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    state = State.load(settings.state_path)
    out = settings.data_dir
    ledger = UsageLedger()
    before = usage.fetch_plan_usage(
        settings.claude_creds, settings.claude_usage_url, settings.fetch_timeout
    )

    ports = []
    findings: List[Finding] = []

    # Stages 0 + 1: portwatch + github + per-port search.
    if args.findings:
        findings = [Finding.from_dict(d) for d in json.loads(args.findings.read_text())]
    else:
        try:
            ports = research.gather(settings, state)
        except portwatch.PortwatchError as exc:
            print(f"research failed: {exc}", file=sys.stderr)
            return 1
        names = ", ".join(f"{p.name} ({p.score}%)" for p in ports)
        print(f"research: top {len(ports)} port(s): {names}")
        if not ports:
            print("research: no ranked ports to report; skipping")
            return 0
        try:
            findings = research.search(settings, state, ports, ledger)
        except ClaudeError as exc:
            print(f"research failed: {exc}", file=sys.stderr)
            return 1

    _write(out / "last-findings.json", json.dumps([f.to_dict() for f in findings], indent=2) + "\n")
    if ports:
        _write(out / "last-ports.json", json.dumps([p.to_dict() for p in ports], indent=2) + "\n")
    print(f"research: {len(findings)} new item(s)")

    snapshots = {p.id: p.snapshot() for p in ports} if ports else None
    if not findings and not settings.always_email and not args.dry_run:
        print("nothing new since last report; skipping email")
        state.record([], port_snapshots=snapshots)
        state.save(settings.state_path)
        return 0

    # Stage 2: review.
    try:
        md = review.summarize(settings, findings, ledger=ledger)
    except ClaudeError as exc:
        print(f"review failed: {exc}", file=sys.stderr)
        return 1

    after = usage.fetch_plan_usage(
        settings.claude_creds, settings.claude_usage_url, settings.fetch_timeout
    )
    footer = ledger.render_footer(before, after)
    full_md = md.rstrip("\n") + ("\n\n" + footer if footer else "") + "\n"
    title = f"GTA 6 PC ports digest - {date.today().isoformat()}"
    html_body = render.to_html(full_md, title)
    _write(out / "last-digest.md", full_md)
    _write(out / "last-digest.html", html_body)
    print(f"review: digest is {review.line_count(md)} line(s) (+ usage footer); "
          f"wrote {out}/last-digest.{{md,html}}")

    if args.dry_run:
        print("--- dry run: digest markdown ---")
        print(full_md)
        return 0

    cfg = mailer.EmailConfig.from_env()
    if cfg is None:
        print("email off: missing " + ", ".join(mailer.missing_keys())
              + " (see .env.example); digest follows")
        print(full_md)
        return 0

    try:
        mailer.send(cfg, mailer.compose(cfg, title, full_md, html_body))
    except smtplib.SMTPAuthenticationError as exc:
        print(f"email failed: SMTP auth rejected (App Password?): {exc}", file=sys.stderr)
        return 2
    except (smtplib.SMTPException, OSError) as exc:
        print(f"email failed: {exc}", file=sys.stderr)
        return 2
    print("emailed MAIL_TO")

    state.record([f.url for f in findings], port_snapshots=snapshots)
    state.save(settings.state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
