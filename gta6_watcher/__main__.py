"""Entry point: search -> review -> render -> email, with cross-run dedup.

Exit codes: 0 = sent, skipped (nothing new), or email not configured;
            1 = a claude stage failed; 2 = SMTP delivery failed.
"""
from __future__ import annotations

import argparse
import json
import smtplib
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import mailer, render, research, review
from .claude_cli import ClaudeError
from .config import Settings
from .research import Finding
from .state import State


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gta6_watcher", description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="run search + review + render, write outputs to the data dir, "
                        "print the markdown; never email and never update state")
    p.add_argument("--findings", type=Path, metavar="FILE",
                   help="skip the search stage and review these findings (JSON list)")
    return p.parse_args(argv)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    state = State.load(settings.state_path)
    out = settings.data_dir

    # Stage 1: research.
    try:
        if args.findings:
            findings = [Finding.from_dict(d) for d in json.loads(args.findings.read_text())]
        else:
            print(f"research: searching since {research.since_date(state)} ({settings.model})")
            findings = research.search(settings, state)
    except ClaudeError as exc:
        print(f"research failed: {exc}", file=sys.stderr)
        return 1
    _write(out / "last-findings.json", json.dumps([f.to_dict() for f in findings], indent=2) + "\n")
    print(f"research: {len(findings)} new item(s)")

    if not findings and not settings.always_email and not args.dry_run:
        print("nothing new since last report; skipping email")
        state.record([])
        state.save(settings.state_path)
        return 0

    # Stage 2: review.
    try:
        md = review.summarize(settings, findings)
    except ClaudeError as exc:
        print(f"review failed: {exc}", file=sys.stderr)
        return 1
    title = f"GTA 6 emulation digest - {date.today().isoformat()}"
    html_body = render.to_html(md, title)
    _write(out / "last-digest.md", md + "\n")
    _write(out / "last-digest.html", html_body)
    print(f"review: digest is {review.line_count(md)} line(s); wrote {out}/last-digest.{{md,html}}")

    if args.dry_run:
        print("--- dry run: digest markdown ---")
        print(md)
        return 0

    cfg = mailer.EmailConfig.from_env()
    if cfg is None:
        print("email off: missing " + ", ".join(mailer.missing_keys())
              + " (see .env.example); digest follows")
        print(md)
        return 0

    try:
        mailer.send(cfg, mailer.compose(cfg, title, md + "\n", html_body))
    except smtplib.SMTPAuthenticationError as exc:
        print(f"email failed: SMTP auth rejected (App Password?): {exc}", file=sys.stderr)
        return 2
    except (smtplib.SMTPException, OSError) as exc:
        print(f"email failed: {exc}", file=sys.stderr)
        return 2
    print("emailed MAIL_TO")

    state.record(f.url for f in findings)
    state.save(settings.state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
