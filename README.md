# gta6-emulation-watcher

A thin daily job that watches the **GTA VI PC-port race** as tracked by
[PortWatch VI](https://portwatch-vi.xyz/) and emails a short digest.

```
portwatch (fetch tracker.json, rank top N) + GitHub lookup (plain HTTP, no claude)
   -> per-port web search (claude -p, WebSearch/WebFetch, structured JSON findings)
      framed against the per-port coverage cache (state.json: score/milestone/summary)
   -> review (claude -p, no tools) -> markdown, hard-capped at 50 lines
   -> append claude-usage footer -> markdown -> HTML (multipart email, plain-text fallback)
   -> SMTP (STARTTLS, Gmail App Password)
```

The agentic stages run on the local `claude` CLI (the box's Claude login), model
`claude-sonnet-5-5` by default (`GTA6_WATCH_MODEL`). The prompts live in
[`prompts/`](prompts/) and can be tuned without touching code. PortWatch scoring is
replicated in [`gta6_watcher/portwatch.py`](gta6_watcher/portwatch.py) (kept in sync
with the site's `scoring.js`) so the top N ports are chosen the same way the site
ranks its race.

## Behaviour
- **Sources:** the top `GTA6_WATCH_TOP_N` (default 5) ranked ports from PortWatch VI,
  each looked up on GitHub (metadata + README head + latest release). A tracker or
  GitHub fetch failure is fatal (exit 1): the digest is about these ports.
- **Runtime scales with `GTA6_WATCH_TOP_N`:** one `claude -p` search call per port
  plus a shared review call (and at most one re-ask). The deploying box's systemd
  `TimeoutStartSec` must comfortably exceed N × `GTA6_WATCH_CLAUDE_TIMEOUT`.
- **Per-port cache:** every run caches, per port, the score / milestone / summary it
  last covered (in `data/state.json`, gitignored). The next run's search prompt tells
  the agent "last covered on {date}; here's what we said" so it reports *how each port
  has developed since* rather than re-reporting the whole history.
- **Claude usage in the email:** each run sums the token counts / wall time / (if the
  CLI reports one) cost across every claude call, and reads the plan's **5-hour and
  weekly usage percentages before and after** the run from Claude's OAuth usage
  endpoint (`https://api.anthropic.com/api/oauth/usage`, using the claude CLI's own
  credential). Both land in a footer on the digest. Claude is subscription-based
  (flat monthly plan), so the footer notes the usage counts toward the plan's limits
  rather than a per-call bill. Any failure reading the windows degrades to "n/a"
  rather than failing the run.
- **Dedup:** URLs already sent are dropped (belt and braces) via `state.seen_urls`.
- **Quiet days:** no new findings means no email (`GTA6_WATCH_ALWAYS_EMAIL=1` to change
  this); the port cache is still updated so the next diff is correct.
- **Email off:** if any of `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO` is unset, the
  digest goes to stdout (the journal under systemd), and the state is left untouched.
- **Exit codes:** `0` sent / skipped / email off, `1` a fetch or claude stage failed,
  `2` SMTP failed.
- Each run leaves `data/last-ports.json`, `data/last-findings.json`,
  `data/last-digest.md` and `data/last-digest.html` for inspection.

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
