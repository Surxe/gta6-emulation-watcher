# gta6-emulation-watcher

A thin daily job that watches for news about **running GTA 6 through PlayStation
emulation** and emails a short digest.

```
research (claude -p, WebSearch/WebFetch, structured JSON findings)
   -> drop URLs already reported (data/state.json)
   -> review (claude -p, no tools) -> markdown, hard-capped at 50 lines
   -> markdown -> HTML (multipart email with plain-text fallback)
   -> SMTP (STARTTLS, Gmail App Password)
```

Both agents run on the local `claude` CLI (the box's Claude login), model
`claude-sonnet-5-5` by default (`GTA6_WATCH_MODEL`). The prompts live in
[`prompts/`](prompts/) and can be tuned without touching code.

## Behaviour
- **Dedup:** each run searches from the last successful report date and is told the
  URLs already sent. State is written only after a successful send (or a quiet day).
- **Quiet days:** no new findings means no email (`GTA6_WATCH_ALWAYS_EMAIL=1` to change this).
- **Email off:** if any of `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO` is unset, the
  digest goes to stdout (the journal under systemd), and the state is left untouched.
- **Exit codes:** `0` sent / skipped / email off, `1` a claude stage failed, `2` SMTP failed.
- Each run leaves `data/last-findings.json`, `data/last-digest.md` and
  `data/last-digest.html` for inspection.

## Usage
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m gta6_watcher --dry-run                  # full run, no email, no state
.venv/bin/python -m gta6_watcher --dry-run --findings F.json  # review/render only
.venv/bin/python -m pytest
```
Settings are all environment variables; see [`.env.example`](.env.example).

## Deployment
This repo carries no box config or secrets. On the home-server, the
[`home-server`](https://github.com/Surxe/home-server) repo owns the deployment:
`gta6-watcher/install.sh` (venv + timer), `systemd/hs-gta6-watch.{service,timer}`
(daily 02:30 America/Chicago), and the secret template for
`/etc/home-server/gta6-watcher.env`.
