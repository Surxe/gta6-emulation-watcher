import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gta6_watcher import __main__ as cli
from gta6_watcher import claude_cli, mailer, render, research, review
from gta6_watcher.config import Settings
from gta6_watcher.research import Finding
from gta6_watcher.state import State

FINDING = {
    "title": "shadPS4 boots GTA 6", "url": "https://example.com/a", "published": "2026-11-18",
    "source": "example", "summary": "It boots.", "status": "claim",
}


def settings(tmp_path, **kw):
    base = dict(model="m", claude_bin="claude", claude_timeout=5,
                data_dir=tmp_path / "data", always_email=False, max_lines=50)
    base.update(kw)
    return Settings(**base)


def stub_claude(tmp_path, envelope, rc=0):
    """A fake `claude` that prints a fixed JSON envelope and logs its argv."""
    script = tmp_path / "claude"
    script.write_text(
        "#!/usr/bin/env python3\nimport json,sys\n"
        f"open({str(tmp_path / 'argv.json')!r},'w').write(json.dumps(sys.argv[1:]))\n"
        "sys.stdin.read()\n"
        f"print({json.dumps(json.dumps(envelope))})\nsys.exit({rc})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


# --- claude_cli -----------------------------------------------------------------

def test_cli_structured_output(tmp_path):
    bin_ = stub_claude(tmp_path, {"is_error": False, "structured_output": {"items": [FINDING]}})
    out = claude_cli.run("p", model="m", claude_bin=bin_, allowed_tools=("WebSearch",),
                         json_schema={"type": "object"})
    assert out["items"][0]["url"] == FINDING["url"]
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch"
    assert "--json-schema" in argv and argv[argv.index("--model") + 1] == "m"


def test_cli_fenced_json_fallback(tmp_path):
    bin_ = stub_claude(tmp_path, {"result": "```json\n{\"items\": []}\n```"})
    assert claude_cli.run("p", model="m", claude_bin=bin_, json_schema={}) == {"items": []}


def test_cli_text_and_errors(tmp_path):
    assert claude_cli.run("p", model="m", claude_bin=stub_claude(tmp_path, {"result": "hi"})) == "hi"
    with pytest.raises(claude_cli.ClaudeError):
        claude_cli.run("p", model="m", claude_bin=stub_claude(tmp_path, {"is_error": True, "result": "x"}))
    with pytest.raises(claude_cli.ClaudeError):
        claude_cli.run("p", model="m", claude_bin=stub_claude(tmp_path, {"result": "x"}, rc=3))
    with pytest.raises(claude_cli.ClaudeError):
        claude_cli.run("p", model="m", claude_bin=str(tmp_path / "nope"))


# --- state + research -----------------------------------------------------------

def test_state_roundtrip_and_dedup(tmp_path):
    path = tmp_path / "s.json"
    s = State.load(path)
    assert s.last_success is None and research.since_date(s)  # lookback default
    s.record(["https://Example.com/a/"], now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    s.save(path)
    s2 = State.load(path)
    assert s2.is_seen("https://example.com/a")
    assert research.since_date(s2) == "2026-11-18"
    assert "https://Example.com/a/" in research.build_prompt(s2)


def test_search_filters_seen(tmp_path, monkeypatch):
    s = State(seen_urls=["https://example.com/a"])
    fresh = dict(FINDING, url="https://example.com/b")
    monkeypatch.setattr(claude_cli, "run", lambda *a, **k: {"items": [FINDING, fresh, {"url": ""}]})
    got = research.search(settings(tmp_path), s)
    assert [f.url for f in got] == ["https://example.com/b"]


# --- review ---------------------------------------------------------------------

def test_review_within_limit_untouched(tmp_path):
    md = review.summarize(settings(tmp_path), [Finding.from_dict(FINDING)],
                          ask=lambda p: "```markdown\n# t\n- a\n```")
    assert md == "# t\n- a"


def test_review_reask_then_trim(tmp_path):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return "\n".join(f"line {i}" for i in range(80))

    md = review.summarize(settings(tmp_path, max_lines=50), [], ask=ask)
    assert len(calls) == 2  # original + one cut request
    assert review.line_count(md) == 50 and md.endswith("*(digest trimmed to fit)*")


def test_review_prompt_contains_findings():
    p = review.build_prompt([Finding.from_dict(FINDING)], 50)
    assert "at most 50 lines" in p and FINDING["url"] in p


# --- render + mailer ------------------------------------------------------------

def test_render_html():
    out = render.to_html("# T\n\n- **x** [link](https://e.com)", "T <&>")
    assert "<title>T &lt;&amp;&gt;</title>" in out
    assert '<a style="color:#0969da;" href="https://e.com">' in out and "<strong>x</strong>" in out


ENV = {"SMTP_USER": "u", "SMTP_PASS": "p", "MAIL_FROM": "f@x", "MAIL_TO": "t@x"}


def test_email_config_requires_all_keys():
    assert mailer.EmailConfig.from_env({}) is None
    assert mailer.missing_keys({"SMTP_USER": "u"}) == ["SMTP_PASS", "MAIL_FROM", "MAIL_TO"]
    cfg = mailer.EmailConfig.from_env(ENV)
    assert (cfg.host, cfg.port) == ("smtp.gmail.com", 587)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.calls = [("connect", host, port)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, u, p):
        self.calls.append(("login", u))

    def send_message(self, msg):
        FakeSMTP.sent.append((self.calls, msg))


def test_mailer_send_multipart():
    cfg = mailer.EmailConfig.from_env(ENV)
    msg = mailer.compose(cfg, "subj", "text", "<p>html</p>")
    mailer.send(cfg, msg, smtp_factory=FakeSMTP)
    calls, sent = FakeSMTP.sent[-1]
    assert calls[1:] == ["starttls", ("login", "u")]
    assert sent["To"] == "t@x" and sent.is_multipart()
    assert {p.get_content_type() for p in sent.iter_parts()} == {"text/plain", "text/html"}


# --- end to end (stages stubbed) ------------------------------------------------

@pytest.fixture
def e2e(tmp_path, monkeypatch):
    monkeypatch.setenv("GTA6_WATCH_DATA_DIR", str(tmp_path / "data"))
    for k in list(ENV) + ["GTA6_WATCH_ALWAYS_EMAIL"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(review, "summarize", lambda s, f: "# digest\n- item")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda cfg, msg: sent.append(msg))
    return tmp_path / "data", sent


def test_main_sends_and_records(e2e, monkeypatch):
    data, sent = e2e
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(research, "search", lambda s, st: [Finding.from_dict(FINDING)])
    assert cli.main([]) == 0
    assert len(sent) == 1
    assert State.load(data / "state.json").is_seen(FINDING["url"])
    assert (data / "last-digest.html").exists()


def test_main_quiet_day_skips(e2e, monkeypatch):
    data, sent = e2e
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(research, "search", lambda s, st: [])
    assert cli.main([]) == 0 and sent == []
    assert State.load(data / "state.json").last_success is not None


def test_main_email_off_keeps_state(e2e, monkeypatch):
    data, sent = e2e
    monkeypatch.setattr(research, "search", lambda s, st: [Finding.from_dict(FINDING)])
    assert cli.main([]) == 0 and sent == []
    assert not (data / "state.json").exists()


def test_main_dry_run_and_findings_file(e2e, tmp_path, monkeypatch):
    data, sent = e2e
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    f = tmp_path / "f.json"
    f.write_text(json.dumps([FINDING]))
    assert cli.main(["--dry-run", "--findings", str(f)]) == 0
    assert sent == [] and not (data / "state.json").exists()


def test_main_claude_failure_exit_1(e2e, monkeypatch):
    def boom(*a):
        raise claude_cli.ClaudeError("x")
    monkeypatch.setattr(research, "search", boom)
    assert cli.main([]) == 1


def test_main_smtp_failure_exit_2(e2e, monkeypatch):
    import smtplib
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(research, "search", lambda s, st: [Finding.from_dict(FINDING)])

    def fail(cfg, msg):
        raise smtplib.SMTPException("down")
    monkeypatch.setattr(mailer, "send", fail)
    assert cli.main([]) == 2
