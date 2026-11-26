import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gta6_watcher import __main__ as cli
from gta6_watcher import claude_cli, github, mailer, portwatch, render, research, review, usage
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


def port(**kw):
    base = dict(
        id="p1", name="ExamplePort", repo="owner/example", branch="main",
        approach="Compatibility layer", target="GTA VI only", score=60.0, reached="playable",
        summary="A port.", highlights=["h1"], blockers=["b1"], verification="self-reported",
        stars=100, forks=20, pushed_at="2026-11-25T00:00:00Z", gta={"playable": 1},
    )
    base.update(kw)
    return portwatch.Port(**base)


def effort(**kw):
    base = dict(
        id="e1", name="Effort", repo="owner/effort", tier="tracked", branch="main",
        gta={}, stats={"stars": 10},
    )
    base.update(kw)
    return base


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


def test_cli_ledger_accumulates_usage(tmp_path):
    env = {
        "is_error": False, "result": "ok", "duration_ms": 1234,
        "usage": {"input_tokens": 100, "output_tokens": 50,
                  "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5},
    }
    ledger = usage.UsageLedger()
    claude_cli.run("p", model="m", claude_bin=stub_claude(tmp_path, env), ledger=ledger)
    assert ledger.calls == 1 and ledger.input_tokens == 100 and ledger.output_tokens == 50
    assert ledger.cache_read_tokens == 20 and ledger.cache_write_tokens == 5
    assert ledger.duration_ms == 1234


# --- usage ----------------------------------------------------------------------

def test_usage_footer_mentions_subscription_and_tokens():
    ledger = usage.UsageLedger(calls=6, input_tokens=41234, output_tokens=7890,
                               cache_read_tokens=12345, duration_ms=184000)
    footer = ledger.render_footer()
    assert "subscription" in footer and "6 claude call(s)" in footer
    assert "41,234 input / 7,890 output" in footer and "184s" in footer


def test_usage_footer_empty_when_no_calls():
    assert usage.UsageLedger().render_footer() == ""


def test_usage_footer_with_plan_windows():
    ledger = usage.UsageLedger(calls=5, input_tokens=100, output_tokens=50)
    before = usage.PlanUsage(five_hour_pct=0.0, weekly_pct=99.0,
                             weekly_resets_at="2026-11-28T09:00:00+00:00")
    after = usage.PlanUsage(five_hour_pct=2.0, weekly_pct=100.0)
    footer = ledger.render_footer(before, after)
    assert "5h window 0% → 2%" in footer and "weekly 99% → 100%" in footer
    assert "resets 2026-11-28" in footer


def test_plan_usage_na_when_missing():
    footer = usage.UsageLedger(calls=1).render_footer(
        usage.PlanUsage(five_hour_pct=None, weekly_pct=None), None)
    assert "5h window n/a → n/a" in footer


def test_fetch_plan_usage_parses_windows(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    payload = json.dumps({
        "five_hour": {"utilization": 12.5, "resets_at": None},
        "seven_day": {"utilization": 40.0, "resets_at": "2026-11-28T09:00:00+00:00"},
    })
    monkeypatch.setattr(usage.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTrackerResp(payload.encode()))
    got = usage.fetch_plan_usage(creds, url="https://example.com/usage", timeout=1)
    assert got.five_hour_pct == 12.5 and got.weekly_pct == 40.0
    assert got.weekly_resets_at == "2026-11-28T09:00:00+00:00"


def test_fetch_plan_usage_missing_creds(tmp_path):
    assert usage.fetch_plan_usage(tmp_path / "nope.json", timeout=1) is None


# --- state ----------------------------------------------------------------------

def test_state_ports_cache_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    s = State.load(path)
    snap = {"name": "P", "repo": "o/p", "score": 60.0, "reached": "playable", "summary": "x"}
    s.record([], port_snapshots={"p1": snap}, now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    s.save(path)
    s2 = State.load(path)
    got = s2.port_snapshot("p1")
    assert got["score"] == 60.0 and got["covered_at"] == "2026-11-18"
    assert s2.last_success is not None


def test_state_roundtrip_and_dedup(tmp_path):
    path = tmp_path / "s.json"
    s = State.load(path)
    assert s.last_success is None and research.since_date(s)  # lookback default
    s.record(["https://Example.com/a/"], now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    s.save(path)
    s2 = State.load(path)
    assert s2.is_seen("https://example.com/a")
    assert research.since_date(s2) == "2026-11-18"


# --- portwatch ------------------------------------------------------------------

def test_score_groundwork_only():
    e = effort(groundwork={"loader": 1, "shaders": 1, "graphics": 1, "proven": 1})
    score, reached = portwatch.score_effort(e)
    assert score == 10.0 and reached == "groundwork"


def test_score_boots():
    e = effort(gta={"loads": 1, "boots": 1})
    score, reached = portwatch.score_effort(e)
    assert score == 20.0 and reached == "boots"


def test_score_complete():
    e = effort(gta={"loads": 1, "boots": 1, "menu": 1, "world": 1, "playable": 1,
                    "story": 1, "render": 1, "fps": 30, "stability": "stable"})
    score, reached = portwatch.score_effort(e)
    assert score == 100.0 and reached == "complete"


def test_ranked_efforts_filters_and_sorts():
    tracker = {"efforts": [
        effort(id="low", name="Low", repo="o/low", tier="tracked", gta={"loads": 1}),
        effort(id="high", name="High", repo="o/high", tier="tracked",
               gta={"loads": 1, "boots": 1, "menu": 1, "world": 1, "playable": 1,
                    "story": 0.5, "render": 0.5, "fps": 5, "stability": "sometimes"}),
        effort(id="watch", name="Watch", repo="o/w", tier="watchlist", gta={"loads": 1}),
        effort(id="nuked", name="Nuked", repo="o/n", tier="tracked", gta={"loads": 1, "boots": 1},
               nuked={"at": "2026-11-23"}),
        effort(id="priv", name="Priv", repo=None, tier="tracked"),
    ]}
    ranked = portwatch.ranked_efforts(tracker)
    assert [e["id"] for e in ranked] == ["high", "low"]


def test_top_ports_limits_and_maps():
    tracker = {"efforts": [
        effort(id="a", name="A", repo="o/a", tier="tracked", gta={"loads": 1, "boots": 1},
               stats={"stars": 5, "forks": 1, "pushedAt": "2026-11-25T00:00:00Z"}),
        effort(id="b", name="B", repo="o/b", tier="tracked", gta={"loads": 1},
               stats={"stars": 9, "forks": 2, "pushedAt": "2026-11-25T00:00:00Z"}),
    ]}
    ports = portwatch.top_ports(tracker, n=2)
    assert [p.id for p in ports] == ["a", "b"]
    assert ports[0].repo == "o/a" and ports[0].url == "https://github.com/o/a"
    assert ports[0].score == 20.0 and ports[0].reached == "boots"


class _FakeTrackerResp:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_fetch_tracker_parse_failure(monkeypatch):
    monkeypatch.setattr(portwatch.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTrackerResp(b"not json"))
    with pytest.raises(portwatch.PortwatchError):
        portwatch.fetch_tracker("https://example.com/tracker.json", timeout=1)


def test_fetch_tracker_missing_efforts(monkeypatch):
    monkeypatch.setattr(portwatch.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTrackerResp(b"{}"))
    with pytest.raises(portwatch.PortwatchError):
        portwatch.fetch_tracker("https://example.com/tracker.json", timeout=1)


# --- github ---------------------------------------------------------------------

class _FakeResp:
    def __init__(self, code, body):
        self.code = code
        self._body = body

    def read(self):
        return self._body


def test_github_fetch_repo_success(tmp_path, monkeypatch):
    meta = json.dumps({
        "description": "d", "stargazers_count": 12, "forks_count": 3,
        "open_issues_count": 4, "pushed_at": "2026-11-26T01:47:20Z",
        "archived": False, "homepage": "", "license": {"spdx_id": "GPL-3.0"},
    })
    rel = json.dumps({"tag_name": "v1", "name": "First", "published_at": "2026-11-25T00:00:00Z"})

    def fake_get(url, headers, timeout):
        if url.endswith("/releases/latest"):
            return 200, rel.encode()
        if url.endswith("/readme"):
            return 200, b"# README head"
        return 200, meta.encode()

    monkeypatch.setattr(github, "_get", fake_get)
    info = github.fetch_repo("o/r")
    assert info.stars == 12 and info.license == "GPL-3.0"
    assert info.latest_release == "v1 · First · 2026-11-25"
    assert info.readme_head == "# README head"
    p = port()
    github.apply_to_port(p, info)
    assert p.github_description == "d" and p.github_readme_head == "# README head"


def test_github_fetch_repo_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(github, "_get", lambda *a, **k: (404, b'{"message":"Not Found"}'))
    info = github.fetch_repo("o/gone")
    assert info.missing is True and info.stars == 0


# --- research -------------------------------------------------------------------

def test_build_port_prompt_has_port_and_since(tmp_path):
    s = State(last_success="2026-11-18T08:30:28+00:00", seen_urls=[])
    p = port(last_covered="2026-11-18", last_score=50.0, last_reached="world")
    prompt = research.build_port_prompt(p, s)
    assert p.name in prompt and p.repo in prompt and "60.0" in prompt
    assert "since we last covered" in prompt.lower() or "published on or after" in prompt
    assert "2026-11-18" in prompt and "50.0" in prompt


def test_search_tags_ports_and_dedups(tmp_path, monkeypatch):
    s = State(seen_urls=["https://example.com/seen"])
    ports = [port(id="p1", name="A", repo="o/a"), port(id="p2", name="B", repo="o/b")]
    responses = iter([
        {"items": [dict(FINDING, url="https://example.com/new"),
                   dict(FINDING, url="https://example.com/seen")]},
        {"items": [dict(FINDING, url="https://example.com/new2")]},
    ])
    monkeypatch.setattr(claude_cli, "run", lambda *a, **k: next(responses))
    got = research.search(settings(tmp_path), s, ports)
    assert [(f.port, f.url) for f in got] == [
        ("p1", "https://example.com/new"), ("p2", "https://example.com/new2")]
    assert all(f.port_name for f in got)


def test_search_filters_seen_legacy(tmp_path, monkeypatch):
    s = State(seen_urls=["https://example.com/a"])
    fresh = dict(FINDING, url="https://example.com/b")
    monkeypatch.setattr(claude_cli, "run",
                        lambda *a, **k: {"items": [FINDING, fresh, {"url": ""}]})
    got = research.search(settings(tmp_path), s, [port()])
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
    f = Finding.from_dict(FINDING)
    p = review.build_prompt([f], 50)
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
    monkeypatch.setattr(review, "summarize", lambda s, f, ledger=None: "# digest\n- item")
    monkeypatch.setattr(usage, "fetch_plan_usage", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mailer, "send", lambda cfg, msg: sent.append(msg))
    return tmp_path / "data", sent


def test_main_sends_and_records(e2e, monkeypatch):
    data, sent = e2e
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(research, "gather", lambda s, st: [port()])
    monkeypatch.setattr(research, "search", lambda s, st, ports, ledger: [Finding.from_dict(FINDING)])
    assert cli.main([]) == 0
    assert len(sent) == 1
    st = State.load(data / "state.json")
    assert st.is_seen(FINDING["url"])
    assert st.port_snapshot("p1")["score"] == 60.0
    assert (data / "last-digest.html").exists()
    assert "# digest" in (data / "last-digest.md").read_text()


def test_main_quiet_day_skips(e2e, monkeypatch):
    data, sent = e2e
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(research, "gather", lambda s, st: [port()])
    monkeypatch.setattr(research, "search", lambda s, st, ports, ledger: [])
    assert cli.main([]) == 0 and sent == []
    st = State.load(data / "state.json")
    assert st.last_success is not None
    assert st.port_snapshot("p1")["covered_at"] is not None


def test_main_email_off_keeps_state(e2e, monkeypatch):
    data, sent = e2e
    monkeypatch.setattr(research, "gather", lambda s, st: [port()])
    monkeypatch.setattr(research, "search", lambda s, st, ports, ledger: [Finding.from_dict(FINDING)])
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
    monkeypatch.setattr(research, "gather", lambda s, st: [port()])

    def boom(*a, **k):
        raise claude_cli.ClaudeError("x")
    monkeypatch.setattr(research, "search", boom)
    assert cli.main([]) == 1


def test_main_portwatch_failure_exit_1(e2e, monkeypatch):
    def boom(*a, **k):
        raise portwatch.PortwatchError("down")
    monkeypatch.setattr(research, "gather", boom)
    assert cli.main([]) == 1


def test_main_smtp_failure_exit_2(e2e, monkeypatch):
    import smtplib
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(research, "gather", lambda s, st: [port()])
    monkeypatch.setattr(research, "search", lambda s, st, ports, ledger: [Finding.from_dict(FINDING)])

    def fail(cfg, msg):
        raise smtplib.SMTPException("down")
    monkeypatch.setattr(mailer, "send", fail)
    assert cli.main([]) == 2
