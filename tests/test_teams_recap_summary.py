"""Behaviour tests for `teams.py recap-summary`, the Recap "AI summary" reader.

The reader runs in-process through main(argv) against a REAL headless Chromium whose one page
shows tests/fixtures/teams_recap/chat.html, served from loopback. The fixture is shaped like the
Teams DOM measured on 2026-10-20: a virtualized chat list whose meeting card renders
only after a scroll, a "View recap" button, Recap tabs, a notes treegrid whose rows expand by a
chevron and carry a timestamp button, a follow-up tree, and Teams' left-navigation tree ahead of
both. The fixture counts every click by control kind, so "clicks nothing else" is measured, not
assumed. No Teams, no network beyond 127.0.0.1, and no token: the token file named in the config
does not exist, so a reader that loaded tokens would fail every test here.

Skips where no Chromium-family binary is found.
"""

from __future__ import annotations

import glob
import http.server
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills" / "teams" / "scripts" / "teams.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "teams_recap"
sys.path.insert(0, str(SCRIPT.parent))
import cdp_client as sso_cdp  # noqa: E402

DAY = "2026-10-19"
CHAT = "[Example] Stand-up"
COLLAB_NEEDLE = "Topic Alpha"
COLLAB_SECRETISH = "collab-body-value-never-printed"
EXPECTED = """Meeting notes
- Topic Alpha: Alex said the alpha work is done.
  - Alpha detail one: the first part shipped.
  - Alpha detail two: the second part is next.
- Topic Beta: Blair raised a beta risk.
  - Beta detail: the risk is the schedule.

Follow-up tasks
- Alpha follow-up: Alex to confirm the release.
- Beta follow-up: Blair to share the plan.
"""
# The reader may bring the Teams tab to the front, or open a Teams tab
# itself and leave it on a blank page afterwards, but it never closes a tab.
FORBIDDEN_METHODS = {
    "Target.closeTarget",
    "Page.bringToFront",
    "Page.close",
    "Input.dispatchMouseEvent",
    "Input.dispatchKeyEvent",
}
PARK_URL = "about:blank"
STARTUP_S = 25.0
EXPANDABLE_ROWS = 2  # the fixture has two level-1 notes rows
READS = 2  # a test that reads the same recap twice


def _chromium() -> str | None:
    names = ("chromium", "chromium-browser", "google-chrome", "chrome-headless-shell")
    found = [shutil.which(n) for n in names]
    found += sorted(
        glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium*/*/chrome*"))
    )
    return next(
        (f for f in found if f and os.access(f, os.X_OK) and Path(f).is_file()), None
    )


CHROMIUM = _chromium()
pytestmark = pytest.mark.skipif(
    CHROMIUM is None,
    reason="no Chromium-family browser or Playwright headless shell found on PATH",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Fixtures(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES), **kwargs)

    def do_GET(self):  # noqa: N802 - the http.server hook name
        if "/readcollabobject/" in self.path:
            body = json.dumps(
                {
                    "id": "obj",
                    "meetingNotes": {"title": COLLAB_NEEDLE, "value": COLLAB_SECRETISH},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


class Browser:
    """One headless Chromium with one page; the test side drives it only to set the stage."""

    def __init__(self, root: Path) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fixtures)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.port = _free_port()
        self.handle = subprocess.Popen(
            [
                CHROMIUM,
                "--headless",
                f"--remote-debugging-port={self.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={root / 'profile'}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        end = time.monotonic() + STARTUP_S
        while True:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/version", timeout=2
                )
                break
            except OSError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.3)

    def page(self) -> dict:
        pages = [
            t for t in sso_cdp.list_targets_http(self.port) if t.get("type") == "page"
        ]
        assert len(pages) == 1, pages
        return pages[0]

    def page_count(self) -> int:
        return sum(
            1 for t in sso_cdp.list_targets_http(self.port) if t.get("type") == "page"
        )

    def _eval_in_page(self, expression: str):
        cdp = sso_cdp.Cdp(sso_cdp.browser_ws_url(self.port))
        try:
            sid = cdp.call(
                "Target.attachToTarget",
                {"targetId": self.page()["id"], "flatten": True},
            )["sessionId"]
            reply = cdp.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                session_id=sid,
            )
            cdp.call("Target.detachFromTarget", {"sessionId": sid})
        finally:
            cdp.close()
        return reply.get("result", {}).get("value")

    def show(self, query: str = "") -> None:
        url = f"{self.base}/chat.html{query}"
        self._eval_in_page(f"location.href = {json.dumps(url)}; true")
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if self._eval_in_page(
                "location.href.includes('chat.html') && document.readyState === 'complete'"
                " && !!window.__fixture"
            ):
                return
            time.sleep(0.1)
        raise AssertionError("the fixture page never loaded")

    def record(self) -> dict:
        return self._eval_in_page("window.__fixture")

    def close(self) -> None:
        try:
            cdp = sso_cdp.Cdp(sso_cdp.browser_ws_url(self.port), timeout=5)
            cdp.call("Browser.close", timeout=5)
        except (sso_cdp.CdpError, OSError):
            pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.handle.poll() is None:
            time.sleep(0.2)
        # Chromium's helper processes can outlive the browser process, so end its whole group.
        try:
            os.killpg(self.handle.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.handle.wait(timeout=10)
        self.server.shutdown()


@pytest.fixture
def browser(tmp_path):
    # Function-scoped, so no browser outlives the test that started it.
    b = Browser(tmp_path)
    yield b
    b.close()


@pytest.fixture
def teams(tmp_path, monkeypatch, browser):
    spec = importlib.util.spec_from_file_location("teams_recap_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "RECAP_POLL_S", 0.05)
    config = tmp_path / "teams.toml"
    write_config(config, tmp_path, cdp_port=browser.port, recap_tab_hosts=["127.0.0.1"])
    monkeypatch.setenv("TEAMS_CONFIG", str(config))
    monkeypatch.delenv("TEAMS_PROFILE", raising=False)
    mod.config_path = config
    return mod


def write_config(path: Path, root: Path, **settings) -> None:
    lines = ["[defaults]", 'timezone = "UTC"']
    lines += [f"{k} = {json.dumps(v)}" for k, v in settings.items()]
    lines += [
        "",
        "[defaults.json-file]",
        f"path = {json.dumps(str(root / 'absent.json'))}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recap(mod, *extra: str) -> int:
    return mod.main(["recap-summary", "--chat", CHAT, "--date", DAY, *extra])


def no_clicks(record: dict) -> bool:
    return not any(v for k, v in record.items() if k.endswith("_clicks"))


# ---------------------------------------------------------------------------- the read


def test_given_the_meeting_chat_open_with_its_card_scrolled_out_when_recap_summary_runs_then_it_writes_the_expanded_ai_summary(
    teams, browser, tmp_path, capsys
):
    browser.show()
    pages = browser.page_count()
    out = tmp_path / "summary.txt"
    rc = recap(teams, "--out", str(out))
    assert rc == 0, capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == EXPECTED
    assert browser.page_count() == pages
    record = browser.record()
    assert record["recap_clicks"] == 1
    assert record["tab_clicks"] == 1
    assert record["chevron_clicks"] == EXPANDABLE_ROWS
    assert record["timestamp_clicks"] == 0
    assert record["other_clicks"] == 0
    assert record["opens"] == 0


def test_given_no_out_file_when_recap_summary_runs_then_the_text_goes_to_stdout(
    teams, browser, capsys
):
    browser.show()
    assert recap(teams) == 0
    assert capsys.readouterr().out == EXPECTED


def test_given_a_row_that_is_only_a_count_badge_when_recap_summary_runs_then_that_row_is_dropped(
    teams, browser, capsys
):
    browser.show()
    assert recap(teams) == 0
    captured = capsys.readouterr()
    lines = [line.strip() for line in captured.out.splitlines()]
    assert "- 12" not in lines
    assert not [line for line in lines if line.lstrip("- ").isdigit()]
    assert "1 count badge dropped" in captured.err


def test_given_an_open_teams_tab_when_recap_summary_runs_then_it_brings_that_tab_forward_and_opens_or_closes_nothing(
    teams, browser, capsys
):
    browser.show()
    pages = browser.page_count()
    assert recap(teams) == 0
    err = capsys.readouterr().err
    line = next(x for x in err.splitlines() if x.startswith("cdp methods: "))
    used = set(line.removeprefix("cdp methods: ").split(", "))
    assert {
        "Target.attachToTarget",
        "Target.activateTarget",
        "Runtime.evaluate",
    } <= used
    assert "Target.createTarget" not in used
    assert "Page.navigate" not in used
    assert not used & FORBIDDEN_METHODS
    assert browser.page_count() == pages


def test_given_no_teams_tab_when_recap_summary_runs_then_it_opens_one_reads_it_and_leaves_it_on_a_blank_page(
    teams, browser, tmp_path, capsys
):
    pages = browser.page_count()
    write_config(
        teams.config_path,
        tmp_path,
        cdp_port=browser.port,
        recap_tab_hosts=["127.0.0.1"],
        recap_open_url=f"{browser.base}/chat.html",
        recap_park_url=PARK_URL,
    )
    rc = recap(teams)
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert captured.out == EXPECTED
    assert "opened a Teams tab" in captured.err
    line = next(x for x in captured.err.splitlines() if x.startswith("cdp methods: "))
    used = set(line.removeprefix("cdp methods: ").split(", "))
    assert {"Target.createTarget", "Page.navigate"} <= used
    assert not used & FORBIDDEN_METHODS
    # The parking navigation commits after the reader has detached.
    end = time.monotonic() + 5
    while True:
        urls = [
            t["url"]
            for t in sso_cdp.list_targets_http(browser.port)
            if t["type"] == "page"
        ]
        if all(u == PARK_URL for u in urls) or time.monotonic() > end:
            break
        time.sleep(0.1)
    assert len(urls) == pages + 1
    assert all(u == PARK_URL for u in urls), urls


# ---------------------------------------------------------------------------- stops and refusals


def test_given_another_chat_open_when_recap_summary_runs_then_it_opens_the_named_chat_and_reads_it(
    teams, browser, capsys
):
    browser.show("?chat=Lunch%20plans")
    pages = browser.page_count()
    assert recap(teams) == 0, capsys.readouterr().err
    captured = capsys.readouterr()
    assert captured.out == EXPECTED
    assert (
        f"opened the chat {CHAT!r} in the Teams tab and left it there" in captured.err
    )
    assert browser.record()["chat_clicks"] == 1
    assert browser.page_count() == pages


def test_given_a_chat_missing_from_the_chat_list_when_recap_summary_runs_then_it_refuses_and_clicks_nothing(
    teams, browser, capsys
):
    browser.show("?chat=Lunch%20plans")
    assert teams.main(["recap-summary", "--chat", "No such chat", "--date", DAY]) == (
        teams.EXIT_REFUSED
    )
    err = capsys.readouterr().err
    assert "shows the chat 'Lunch plans'" in err
    assert "no 'No such chat' row to open" in err
    assert no_clicks(browser.record())


def test_given_the_meeting_card_never_renders_when_recap_summary_runs_then_it_stops_without_clicking(
    teams, browser, capsys
):
    browser.show("?card=never")
    assert recap(teams) == 1
    err = capsys.readouterr().err
    assert "no 'View recap' card for October 19, 2026" in err
    assert no_clicks(browser.record())


def test_given_recap_left_open_by_a_previous_read_when_recap_summary_runs_then_it_reopens_the_chat_and_reads_again(
    teams, browser, capsys
):
    browser.show()
    assert recap(teams) == 0
    capsys.readouterr()
    assert recap(teams) == 0, capsys.readouterr().err
    assert capsys.readouterr().out == EXPECTED
    record = browser.record()
    assert record["chat_clicks"] == 1
    assert record["recap_clicks"] == READS


def test_given_an_ai_summary_without_meeting_notes_when_recap_summary_runs_then_it_fails_and_writes_nothing(
    teams, browser, tmp_path, capsys
):
    browser.show("?notes=missing")
    out = tmp_path / "summary.txt"
    assert recap(teams, "--out", str(out)) == 1
    assert "Meeting notes" in capsys.readouterr().err
    assert not out.exists()


def test_given_an_existing_out_file_when_recap_summary_runs_without_force_then_it_refuses_before_touching_the_tab(
    teams, browser, tmp_path
):
    browser.show()
    out = tmp_path / "summary.txt"
    out.write_text("kept\n", encoding="utf-8")
    assert recap(teams, "--out", str(out)) == teams.EXIT_REFUSED
    assert out.read_text(encoding="utf-8") == "kept\n"
    assert no_clicks(browser.record())


# ---------------------------------------------------------------------------- port and network


def test_given_no_cdp_port_in_teams_config_when_recap_summary_runs_then_it_exits_3_naming_the_setting(
    teams, tmp_path, capsys
):
    write_config(teams.config_path, tmp_path, recap_tab_hosts=["127.0.0.1"])
    assert recap(teams) == teams.EXIT_CONFIG
    assert "cdp_port" in capsys.readouterr().err


def test_given_a_port_flag_and_no_cdp_port_in_config_when_recap_summary_runs_then_the_flag_is_used(
    teams, browser, tmp_path, capsys
):
    browser.show()
    write_config(teams.config_path, tmp_path, recap_tab_hosts=["127.0.0.1"])
    assert recap(teams, "--port", str(browser.port)) == 0, capsys.readouterr().err


def test_given_collab_needle_when_recap_opens_then_only_the_response_keys_and_needle_presence_are_reported(
    teams, browser, capsys
):
    browser.show("?collab=1")
    assert recap(teams, "--collab-needle", COLLAB_NEEDLE) == 0
    captured = capsys.readouterr()
    report = next(
        x for x in captured.err.splitlines() if x.startswith("readcollabobject: ")
    )
    assert "status 200" in report
    assert "top-level keys ['id', 'meetingNotes']" in report
    assert "contains the needle: yes" in report
    assert COLLAB_SECRETISH not in captured.out + captured.err
