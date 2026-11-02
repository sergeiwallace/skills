"""Behaviour tests for skills/teams/scripts/teams.py, driven through main(argv).

No network: the module's HTTP client is replaced by a fake that answers by URL prefix.
The token provider is the real json-file provider reading a token file in tmp_path, and
the settings come from a real TOML file named by TEAMS_CONFIG, so the config and provider
paths run as shipped. Every id, URL and name below is synthetic.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills" / "teams" / "scripts" / "teams.py"

# One distinct fake per audience, so a test can tell which token a call carried.
TOKENS = {
    "chat_service": "fake-chat-service-token-0123456789",
    "ic3": "fake-ic3-token-0123456789abcdef",
    "graph": "fake-graph-token-0123456789abcdef",
    "sharepoint": "fake-sharepoint-token-0123456789",
}
ALL_TOKENS = tuple(TOKENS.values())
CONV = "19:meeting_example@thread.v2"
TENANT = "https://example-my.sharepoint.com"
OBJ_TODAY = "0-eus-d3-today0000000000000000000"
OBJ_YESTERDAY = "0-eus-d3-yesterday00000000000000"
GATEWAY_VTT = b"WEBVTT\r\n\r\n00:00:01.000 --> 00:00:05.000\r\n<v Alex Example>Morning. Start?</v>\r\n"
UI_VTT = (
    b"WEBVTT\r\n\r\ncall-leg/1-0\r\n00:00:01.000 --> 00:00:03.000\r\n<v Alex Example>Morning.</v>\r\n"
    b"\r\ncall-leg/2-0\r\n00:00:03.000 --> 00:00:05.000\r\n<v Alex Example>Start?</v>\r\n"
)
BOM = b"\xef\xbb\xbf"
TID_TODAY, TID_YESTERDAY = "fake-transcript-today", "fake-transcript-yesterday"
ODSP_QUERY = "/streamContent?is=1&applymediaedits=false"
REC_DRIVE = "b!FAKERECDRIVE0000"
REC_ITEM = "01FAKERECITEM000000000000000000"
REC_NAME = "Team Stand-up-20261019_140100UTC-Meeting Recording.mp4"
MP4_BODY = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4096
DAY = "2026-10-19"
TITLE = "Team Stand-up"
STEM = f"{TITLE} {DAY}"


def _b64url(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


EXAMPLE_NAV = _b64url(
    "s=%2Fpersonal%2Forganizer_example_com&d=b!FAKEDRIVEID0000&f=01FAKEITEMID00000000000000000000"
    "&c=%2F&fluid=1&a=Teams&p=%40fluidx%2Floop-page-container"
)
EXAMPLE_INNER = (
    f"{TENANT}/:fl:/g/personal/organizer_example_com/FAKEFILEID?nav={EXAMPLE_NAV}"
)
EXAMPLE_LOOP = "https://loop.cloud.microsoft/p/" + _b64url(
    json.dumps({"p": {"u": EXAMPLE_INNER, "r": True}})
)


def odsp_transcript(tid: str) -> str:
    return (
        f"{TENANT}/personal/organizer_example_com/_api/v2.1/drives/{REC_DRIVE}/items/{REC_ITEM}"
        f"/versions/current/media/transcripts/{tid}/content"
    )


def recording(oid: str, when: str, mid: str, tid: str) -> dict:
    uri = f"https://us-prod.asyncgw.teams.microsoft.com/v1/objects/{oid}/views/video"
    share = f"{TENANT}/:v:/g/personal/organizer_example_com/FAKEVIDEO"
    content = (
        f'<URIObject uri="{uri}" url_thumbnail="{TENANT}/personal/rec.mp4">'
        f'<RecordingStatus status="Success" code="200" /><OriginalName v="{REC_NAME}" />'
        f'<item type="amsVideo" uri="{uri}" />'
        f'<item type="onedriveForBusinessVideo" uri="{share}" driveId="{REC_DRIVE}" '
        f'driveItemId="{REC_ITEM}" />'
        f'<item type="onedriveForBusinessTranscript" uri="{odsp_transcript(tid)}" id="{tid}" />'
        "</URIObject>"
    )
    return {
        "id": mid,
        "originalarrivaltime": when,
        "messagetype": "RichText/Media_CallRecording",
        "imdisplayname": "Alex Example",
        "content": content,
    }


THREAD = [
    # newest first, as the chat service returns them
    {
        "id": "late-local",
        "originalarrivaltime": "2026-10-20T02:30:00.000Z",  # 22:30 EDT on 2026-10-19
        "messagetype": "RichText/Html",
        "imdisplayname": "Alex Example",
        "content": "<p>late note</p>",
    },
    {
        "id": "loop-msg",
        "originalarrivaltime": "2026-10-19T14:30:00.000Z",
        "messagetype": "RichText/Html",
        "imdisplayname": "Facilitator",
        "content": f'<p>Notes: <a href="{EXAMPLE_LOOP}">Loop</a></p>',
    },
    recording(OBJ_TODAY, "2026-10-19T14:05:00.000Z", "rec-today", TID_TODAY),
    {
        "id": "prev-evening",
        "originalarrivaltime": "2026-10-19T03:00:00.000Z",  # 23:00 EDT on 2026-10-18
        "messagetype": "RichText/Html",
        "imdisplayname": "Sam Sample",
        "content": "<p>evening before</p>",
    },
    recording(
        OBJ_YESTERDAY, "2026-10-18T14:05:00.000Z", "rec-yesterday", TID_YESTERDAY
    ),
]

CHATS = [
    {"id": CONV, "threadProperties": {"topic": "Team Stand-up"}},
    {
        "id": "19:other@thread.v2",
        "threadProperties": {"spaceThreadTopic": "Lunch plans"},
    },
    {"id": "19:aaaa_bbbb@unq.gbl.spaces", "threadProperties": {}},
]


class Resp:
    def __init__(self, status: int, body=b"", payload=None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300  # noqa: PLR2004
        self.content = body if isinstance(body, bytes) else body.encode()
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    """Answers by the first URL fragment that matches; records every call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.routes: dict[str, Resp] = {}

    def _answer(self, url: str) -> Resp:
        for fragment, resp in self.routes.items():
            if fragment in url:
                return resp
        return Resp(404, b"", {"error": {"code": "itemNotFound"}})

    def get(self, url, headers, timeout):  # noqa: ARG002
        self.calls.append({"method": "GET", "url": url, "headers": dict(headers)})
        return self._answer(url)

    def post_json(self, url, headers, payload, timeout):  # noqa: ARG002
        self.calls.append(
            {"method": "POST", "url": url, "headers": dict(headers), "json": payload}
        )
        return self._answer(url)

    def download(self, url, headers, dest, timeout):  # noqa: ARG002
        self.calls.append({"method": "GET", "url": url, "headers": dict(headers)})
        resp = self._answer(url)
        if resp.ok:
            Path(dest).write_bytes(resp.content)
        return resp

    def urls(self, method: str = "GET") -> list[str]:
        return [c["url"] for c in self.calls if c["method"] == method]


def write_config(path: Path, token_file: Path, **overrides) -> None:
    settings = {
        "provider": "json-file",
        "login_hint": "Run: example-broker login",
        "timezone": "America/New_York",
        "title": TITLE,
        "out_root": str(path.parent / "out"),
    }
    settings.update(overrides)
    lines = ["[defaults]"]
    lines += [f"{k} = {json.dumps(v)}" for k, v in settings.items()]
    lines += ["", "[defaults.json-file]", f"path = {json.dumps(str(token_file))}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Env:
    def __init__(self, mod, http: FakeHttp, root: Path) -> None:
        self.mod = mod
        self.http = http
        self.root = root
        self.config = root / "teams.toml"
        self.token_file = root / "tokens.json"

    def write_tokens(self, **tokens) -> None:
        data = {**TOKENS, "region": "amer", **tokens}
        self.token_file.write_text(json.dumps(data), encoding="utf-8")

    def configure(self, **overrides) -> None:
        write_config(self.config, self.token_file, **overrides)

    def data_dir(self) -> Path:
        return self.root / "out" / DAY / "data"


@pytest.fixture
def env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("teams_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(
        sys.modules, spec.name, mod
    )  # dataclasses resolve annotations here
    spec.loader.exec_module(mod)
    http = FakeHttp()
    http.routes["/conversations/19%3A"] = Resp(200, b"", {"messages": THREAD})
    http.routes[f"/objects/{OBJ_TODAY}/views/transcript"] = Resp(200, GATEWAY_VTT)
    http.routes[f"/objects/{OBJ_YESTERDAY}/views/transcript"] = Resp(
        200, b"WEBVTT\r\n\r\nno\r\n"
    )
    http.routes[f"/transcripts/{TID_TODAY}{ODSP_QUERY}"] = Resp(200, BOM + UI_VTT)
    http.routes[f"/transcripts/{TID_YESTERDAY}{ODSP_QUERY}"] = Resp(
        200, BOM + b"WEBVTT\r\n\r\nno\r\n"
    )
    http.routes["/conversations?pageSize="] = Resp(200, b"", {"conversations": CHATS})
    monkeypatch.setattr(mod, "HTTP", http)
    e = Env(mod, http, tmp_path)
    e.write_tokens()
    e.configure()
    monkeypatch.setenv("TEAMS_CONFIG", str(e.config))
    monkeypatch.delenv("TEAMS_PROFILE", raising=False)
    return e


# ---------------------------------------------------------------------------- reads


def test_given_chats_when_chats_runs_with_a_query_then_only_matching_topics_print(
    env, capsys
):
    rc = env.mod.main(["chats", "--query", "stand-up"])
    out = capsys.readouterr().out
    assert rc == 0
    assert CONV in out and "Team Stand-up" in out
    assert "Lunch plans" not in out
    assert env.http.calls[0]["headers"] == {
        "Authentication": f"skypetoken={TOKENS['chat_service']}"
    }
    assert env.http.calls[0]["url"].startswith(
        "https://amer.ng.msg.teams.microsoft.com/"
    )


def test_given_a_thread_when_messages_runs_then_senders_and_text_print_without_markup(
    env, capsys
):
    rc = env.mod.main(["messages", "--conv", CONV])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Facilitator: Notes: Loop" in out
    assert "<p>" not in out


def test_given_a_recurring_thread_when_thread_filters_a_date_then_only_that_local_date_remains(
    env, capsys
):
    rc = env.mod.main(["thread", "--conv", CONV, "--date", DAY])
    out = capsys.readouterr().out
    assert rc == 0
    ids = [line.split("\t")[0] for line in out.splitlines()]
    assert ids == ["late-local", "loop-msg", "rec-today"]


def test_given_a_timezone_edit_between_calls_when_thread_runs_then_the_new_value_applies(
    env, capsys
):
    env.mod.main(["thread", "--conv", CONV, "--date", DAY])
    capsys.readouterr()
    env.configure(timezone="UTC")
    env.mod.main(["thread", "--conv", CONV, "--date", DAY])
    ids = [line.split("\t")[0] for line in capsys.readouterr().out.splitlines()]
    assert ids == ["loop-msg", "rec-today", "prev-evening"]


def test_given_a_dated_recording_when_vtt_runs_then_the_download_file_lands_dated_without_bom(
    env, tmp_path, capsys
):
    rc = env.mod.main(["vtt", "--conv", CONV, "--date", DAY, "--out", str(tmp_path)])
    target = tmp_path / f"{STEM}.vtt"
    assert rc == 0
    assert target.read_bytes() == UI_VTT
    asked = [c for c in env.http.calls if "/transcript" in c["url"]]
    assert [c["url"] for c in asked] == [
        odsp_transcript(TID_TODAY)[: -len("/content")] + ODSP_QUERY
    ]
    assert asked[0]["headers"] == {"Authorization": f"Bearer {TOKENS['sharepoint']}"}
    assert TID_TODAY in capsys.readouterr().out


def test_given_the_onedrive_transcript_refuses_when_vtt_runs_then_the_gateway_view_lands_with_a_warning(
    env, tmp_path, capsys
):
    env.http.routes[f"/transcripts/{TID_TODAY}{ODSP_QUERY}"] = Resp(
        403, b"", {"error": {"code": "accessDenied"}}
    )
    rc = env.mod.main(["vtt", "--conv", CONV, "--date", DAY, "--out", str(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert (tmp_path / f"{STEM}.vtt").read_bytes() == GATEWAY_VTT
    assert OBJ_TODAY in out.out
    assert "OneDrive transcript: 403 accessDenied" in out.err
    assert "merges each speaker's consecutive cues" in out.err
    gateway = next(c for c in env.http.calls if "/views/transcript" in c["url"])
    assert gateway["url"].startswith("https://us-prod.asyncgw.teams.microsoft.com/")
    assert gateway["headers"] == {"Authorization": f"Bearer {TOKENS['ic3']}"}


def test_given_an_existing_vtt_when_vtt_runs_without_force_then_it_refuses_and_keeps_the_file(
    env, tmp_path, capsys
):
    target = tmp_path / f"{STEM}.vtt"
    target.write_bytes(b"keep me")
    rc = env.mod.main(["vtt", "--conv", CONV, "--date", DAY, "--out", str(tmp_path)])
    assert rc == env.mod.EXIT_REFUSED
    assert target.read_bytes() == b"keep me"
    assert "--force" in capsys.readouterr().err
    argv = ["vtt", "--conv", CONV, "--date", DAY, "--out", str(tmp_path), "--force"]
    assert env.mod.main(argv) == 0
    assert target.read_bytes() == UI_VTT


def test_given_a_day_with_no_recording_when_vtt_runs_then_it_fails_without_writing(
    env, tmp_path
):
    rc = env.mod.main(
        ["vtt", "--conv", CONV, "--date", "2026-10-17", "--out", str(tmp_path)]
    )
    assert rc == env.mod.EXIT_FAIL
    assert list(tmp_path.glob("*.vtt")) == []


def test_given_the_example_loop_link_when_loop_links_runs_then_it_decodes_and_resolves_the_inner_url(
    env, capsys
):
    item = {
        "name": "Notes.loop",
        "size": 1,
        "id": "ITEM",
        "webUrl": "w",
        "parentReference": {"driveId": "D"},
    }
    env.http.routes["/shares/"] = Resp(200, b"", item)
    rc = env.mod.main(["loop-links", "--conv", CONV, "--date", DAY])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"inner_url: {TENANT}/:fl:/g/personal/" in out
    assert "nav.f (itemId): 01FAKEITEMID00000000000000000000" in out
    shares = [c for c in env.http.calls if "/shares/" in c["url"]]
    assert [c["url"] for c in shares] == [
        f"https://graph.microsoft.com/v1.0/shares/{env.mod.share_id(EXAMPLE_INNER)}/driveItem"
    ]
    assert shares[0]["headers"] == {"Authorization": f"Bearer {TOKENS['graph']}"}


def test_given_graph_rejects_both_forms_when_loop_links_runs_then_each_failure_is_reported(
    env, capsys
):
    rc = env.mod.main(["loop-links", "--conv", CONV, "--date", DAY])
    out = capsys.readouterr().out
    assert rc == env.mod.EXIT_FAIL
    assert "resolve (inner): FAILED 404 itemNotFound" in out
    assert "resolve (link): FAILED 404 itemNotFound" in out


def test_given_a_dated_recording_when_recording_uris_runs_then_each_uri_is_classified(
    env, capsys
):
    rc = env.mod.main(["recording-uris", "--conv", CONV, "--date", DAY])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"gateway object: {OBJ_TODAY}" in out
    assert OBJ_YESTERDAY not in out
    assert "host: async media gateway" in out
    assert "host: sharepoint/onedrive" in out


def test_given_a_date_older_than_the_fetched_page_when_vtt_runs_then_it_warns_instead_of_reporting_empty(
    env, tmp_path, capsys
):
    rc = env.mod.main(
        ["vtt", "--conv", CONV, "--date", "2026-09-01", "--out", str(tmp_path)]
    )
    assert rc == env.mod.EXIT_FAIL
    assert "older than the fetched window" in capsys.readouterr().err


class _RedirectingHandler(http.server.BaseHTTPRequestHandler):
    seen: dict[str, dict] = {}

    def do_GET(self):  # noqa: N802
        _RedirectingHandler.seen[self.path] = dict(self.headers)
        if self.path == "/item/content":
            self.send_response(302)
            self.send_header(
                "Location", f"http://localhost:{self.server.server_port}/download"
            )
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(MP4_BODY)

    def log_message(self, *_args):
        pass


def test_given_a_redirect_to_another_host_when_the_real_client_downloads_then_the_bearer_is_not_forwarded(
    tmp_path,
):
    spec = importlib.util.spec_from_file_location("teams_http_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            dest = tmp_path / "rec.mp4"
            url = f"http://127.0.0.1:{server.server_port}/item/content"
            resp = mod.UrllibHttp().download(
                url, {"Authorization": "Bearer " + TOKENS["graph"]}, dest, 10
            )
        finally:
            server.shutdown()
            server.server_close()
    finally:
        sys.modules.pop(spec.name, None)
    assert resp.ok
    assert dest.read_bytes() == MP4_BODY
    assert (
        _RedirectingHandler.seen["/item/content"].get("Authorization")
        == "Bearer " + TOKENS["graph"]
    )
    assert "Authorization" not in _RedirectingHandler.seen["/download"]


# ---------------------------------------------------------------------------- provider and config


def test_given_no_token_file_when_a_read_runs_then_it_exits_4_with_the_configured_login_hint(
    env, capsys
):
    env.token_file.unlink()
    rc = env.mod.main(["thread", "--conv", CONV])
    err = capsys.readouterr().err
    assert rc == env.mod.EXIT_AUTH
    assert "Run: example-broker login" in err


def test_given_a_token_file_missing_one_audience_when_vtt_needs_it_then_it_exits_4(
    env, tmp_path
):
    env.write_tokens(sharepoint="", ic3="")
    rc = env.mod.main(["vtt", "--conv", CONV, "--date", DAY, "--out", str(tmp_path)])
    assert rc == env.mod.EXIT_AUTH
    assert list(tmp_path.glob("*.vtt")) == []


def test_given_a_broker_adapter_in_config_when_a_read_runs_then_its_tokens_and_region_are_used(
    env, tmp_path, capsys
):
    adapters = tmp_path / "adapters"
    adapters.mkdir()
    (adapters / "example_adapter_mod.py").write_text(
        "def teams_tokens(settings):\n"
        "    return {'chat_service': 'adapter-chat-token-000', 'ic3': 'adapter-ic3-token-000',\n"
        "            'graph': 'adapter-graph-token-000', 'sharepoint': 'adapter-sp-token-000',\n"
        "            'region': 'emea'}\n",
        encoding="utf-8",
    )
    env.configure(
        provider="example_adapter_mod:teams_tokens", provider_path=str(adapters)
    )
    rc = env.mod.main(["chats", "--query", "stand-up"])
    out = capsys.readouterr().out
    assert rc == 0
    assert env.http.calls[0]["url"].startswith(
        "https://emea.ng.msg.teams.microsoft.com/"
    )
    assert env.http.calls[0]["headers"] == {
        "Authentication": "skypetoken=adapter-chat-token-000"
    }
    assert "adapter-chat-token-000" not in out


def test_given_an_unknown_provider_when_a_read_runs_then_it_exits_3_naming_it(
    env, capsys
):
    env.configure(provider="no_such_adapter_module:teams_tokens")
    rc = env.mod.main(["thread", "--conv", CONV])
    assert rc == env.mod.EXIT_CONFIG
    assert "no_such_adapter_module" in capsys.readouterr().err


def test_given_a_profile_table_when_teams_profile_matches_then_it_overrides_defaults(
    env, monkeypatch, capsys
):
    with env.config.open("a", encoding="utf-8") as fh:
        fh.write('\n[profiles.example-profile]\ntimezone = "UTC"\n')
    monkeypatch.setenv("TEAMS_PROFILE", "example-profile")
    env.mod.main(["thread", "--conv", CONV, "--date", DAY])
    ids = [line.split("\t")[0] for line in capsys.readouterr().out.splitlines()]
    assert ids == ["loop-msg", "rec-today", "prev-evening"]


def test_given_the_help_flag_when_parsed_then_no_token_is_read(env, capsys):
    env.token_file.unlink()
    with pytest.raises(SystemExit) as exc:
        env.mod.main(["--help"])
    assert exc.value.code == 0
    assert env.http.calls == []
    assert "send" in capsys.readouterr().out


# ---------------------------------------------------------------------------- the one write


def body_file(tmp_path: Path, text: str = "<p>Notes are up.</p>") -> tuple[Path, str]:
    f = tmp_path / "msg.html"
    f.write_text(text, encoding="utf-8")
    return f, hashlib.sha256(f.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "text,finding",
    [
        (
            "<p>See https://example.invalid/browse/ISSUE-1 for the story.</p>",
            "bare URL https://example.invalid/browse/ISSUE-1",
        ),
        (
            '<p>See <a href="https://example.invalid/browse/ISSUE-1">https://example.invalid/browse/ISSUE-1</a>.</p>',
            "link https://example.invalid/browse/ISSUE-1 has no readable display text",
        ),
    ],
    ids=["bare-url", "url-as-its-own-text"],
)
def test_given_a_url_without_display_text_when_send_previews_then_it_refuses_naming_it(
    env, tmp_path, capsys, text, finding
):
    f, _ = body_file(tmp_path, text)
    rc = env.mod.main(["send", "--conv", CONV, "--file", str(f)])
    err = capsys.readouterr().err
    assert rc == env.mod.EXIT_REFUSED
    assert finding in err and "hyperlink with readable display text" in err
    assert env.http.calls == []


def test_given_a_linked_url_with_display_text_when_send_previews_then_it_previews(
    env, tmp_path, capsys
):
    f, digest = body_file(
        tmp_path,
        '<p>See <a href="https://example.invalid/browse/ISSUE-1">ISSUE-1</a>.</p>',
    )
    rc = env.mod.main(["send", "--conv", CONV, "--file", str(f)])
    assert rc == 0 and digest in capsys.readouterr().out


def test_given_a_bare_url_and_the_waiver_when_send_previews_then_it_says_the_rule_was_waived(
    env, tmp_path, capsys
):
    f, _ = body_file(tmp_path, "<p>See https://example.invalid/x now.</p>")
    rc = env.mod.main(["send", "--conv", CONV, "--file", str(f), "--allow-bare-urls"])
    assert rc == 0
    assert (
        "link rule waived by --allow-bare-urls: bare URL https://example.invalid/x"
        in capsys.readouterr().out
    )


def test_given_a_body_when_send_runs_without_send_then_it_previews_posts_nothing_and_reads_no_token(
    env, tmp_path, capsys
):
    env.token_file.unlink()
    f, digest = body_file(tmp_path)
    rc = env.mod.main(["send", "--conv", CONV, "--file", str(f)])
    out = capsys.readouterr().out
    assert rc == 0
    assert env.http.calls == []
    assert CONV in out and digest in out and "<p>Notes are up.</p>" in out
    assert "nothing was posted" in out


def test_given_a_wrong_sha256_when_send_runs_then_it_refuses_and_posts_nothing(
    env, tmp_path, capsys
):
    f, _ = body_file(tmp_path)
    rc = env.mod.main(
        ["send", "--conv", CONV, "--file", str(f), "--send", "--sha256", "0" * 64]
    )
    assert rc == env.mod.EXIT_REFUSED
    assert env.http.urls("POST") == []
    assert "refused" in capsys.readouterr().err


def test_given_send_without_sha256_when_send_runs_then_it_refuses(env, tmp_path):
    f, _ = body_file(tmp_path)
    assert (
        env.mod.main(["send", "--to-notes", "--file", str(f), "--send"])
        == env.mod.EXIT_REFUSED
    )
    assert env.http.urls("POST") == []


def test_given_the_approved_sha256_when_send_runs_then_the_exact_body_is_posted_once(
    env, tmp_path, capsys
):
    env.http.routes["/conversations/48%3Anotes/messages"] = Resp(
        201, b"", {"OriginalArrivalTime": 1}
    )
    f, digest = body_file(tmp_path)
    rc = env.mod.main(
        ["send", "--to-notes", "--file", str(f), "--send", "--sha256", digest]
    )
    posts = [c for c in env.http.calls if c["method"] == "POST"]
    assert rc == 0
    assert len(posts) == 1
    assert (
        posts[0]["url"]
        == "https://amer.ng.msg.teams.microsoft.com/v1/users/ME/conversations/48%3Anotes/messages"
    )
    assert posts[0]["json"] == {
        "content": "<p>Notes are up.</p>",
        "messagetype": "RichText/Html",
        "contenttype": "html",
    }
    assert (
        posts[0]["headers"]["Authentication"] == f"skypetoken={TOKENS['chat_service']}"
    )
    assert "sent" in capsys.readouterr().out


def test_given_the_chat_service_rejects_the_send_when_send_runs_then_it_exits_4(
    env, tmp_path
):
    env.http.routes["/conversations/48%3Anotes/messages"] = Resp(401, b"denied")
    f, digest = body_file(tmp_path)
    rc = env.mod.main(
        ["send", "--to-notes", "--file", str(f), "--send", "--sha256", digest]
    )
    assert rc == env.mod.EXIT_AUTH


# ---------------------------------------------------------------------------- land

GRAPH_PDF = b"%PDF-1.7 graph export"
NOTES = '<h1 style="m"><span>Meeting notes</span></h1><div><span>Facilitator notes body</span></div>'
FOLLOW_UP = '<h1 style="m"><span>Follow-up tasks</span></h1><table><tr><td>task</td></tr></table>'
AI_DONE = (
    '<h1 style="m"><span>AI Summary</span></h1><div><span>pasted by hand</span></div>'
)
LOOP_DRIVE, LOOP_ITEM = "b!FAKELOOPDRIVE000", "01FAKELOOPITEM0000000000000000"
SUMMARY_TEXT = (
    "Meeting notes:\n"
    "- Topic One: Alex explained R&D storage.\n"
    "  - Detail A: more about it.\n"
    "- Topic Two: Sam shipped it.\n"
    "\n"
    "Follow-up tasks:\n"
    "- Write the doc. (Alex)\n"
)


def loop_html(*parts: str) -> bytes:
    return ("<html><body>" + "".join(parts) + "</body></html>").encode()


def arrange_land(env, page: bytes) -> None:
    env.http.routes["/shares/"] = Resp(
        200,
        b"",
        {
            "name": f"{STEM}.loop",
            "size": 1,
            "id": LOOP_ITEM,
            "parentReference": {"driveId": LOOP_DRIVE},
        },
    )
    env.http.routes[f"/items/{LOOP_ITEM}/content?format=html"] = Resp(200, page)
    env.http.routes[f"/items/{LOOP_ITEM}/content?format=pdf"] = Resp(200, GRAPH_PDF)
    env.http.routes[f"/items/{REC_ITEM}/content"] = Resp(200, MP4_BODY)
    env.http.routes[f"/drives/{REC_DRIVE}/items/{REC_ITEM}?"] = Resp(
        200, b"", {"name": REC_NAME, "size": len(MP4_BODY)}
    )


def fake_chromium(tmp_path: Path) -> tuple[Path, Path]:
    """Stands in for headless Chromium: copies the file:// page into the --print-to-pdf target."""
    log = tmp_path / "chromium-calls.txt"
    exe = tmp_path / "fake-chromium"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import sys, urllib.parse\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "out = next(a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--print-to-pdf='))\n"
        "src = urllib.parse.unquote(sys.argv[-1][len('file://'):])\n"
        "open(out, 'wb').write(b'%PDF-local ' + open(src, 'rb').read())\n"
    )
    exe.chmod(0o755)
    return exe, log


def summary_file(tmp_path: Path) -> Path:
    path = tmp_path / "ai-summary.txt"
    path.write_text(SUMMARY_TEXT, encoding="utf-8")
    return path


def run_land(env, *extra: str) -> int:
    return env.mod.main(["land", "--conv", CONV, "--date", DAY, *extra])


def test_given_a_loop_page_with_the_ai_summary_when_land_runs_then_all_artefacts_land_in_the_configured_dir(
    env, tmp_path, capsys
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    chromium, log = fake_chromium(tmp_path)
    rc = run_land(env, "--chromium", str(chromium))
    data = env.data_dir()
    assert rc == 0
    assert (data / f"{STEM}.vtt").read_bytes() == UI_VTT
    assert (data / REC_NAME).read_bytes() == MP4_BODY
    assert (data / f"{STEM}.html").read_bytes() == loop_html(NOTES, AI_DONE, FOLLOW_UP)
    assert (data / f"{STEM}.pdf").read_bytes() == GRAPH_PDF
    assert not log.exists()
    assert "already in the Loop page" in capsys.readouterr().out


def test_given_a_loop_page_without_the_ai_summary_and_a_summary_file_when_land_runs_then_the_pdf_is_composed(
    env, tmp_path
):
    arrange_land(env, loop_html(NOTES, FOLLOW_UP))
    chromium, log = fake_chromium(tmp_path)
    rc = run_land(
        env, "--chromium", str(chromium), "--ai-summary", str(summary_file(tmp_path))
    )
    data = env.data_dir()
    assert rc == 0
    page = (data / f"{STEM}.pdf").read_bytes().decode()
    assert page.startswith("%PDF-local ")
    h1 = re.search(r"<h1[^>]*><span>AI Summary</span></h1>", page).start()
    notes, follow = (
        page.index("Facilitator notes body"),
        page.index(">Follow-up tasks<"),
    )
    assert notes < h1 < follow
    section = page[h1:follow]
    assert "Topic One: Alex explained R&amp;D storage." in section
    one = section.index("Topic One")
    assert (
        one
        < section.index("<ul", one)
        < section.index("Detail A")
        < section.index("Topic Two")
    )
    assert (data / f"{STEM}.html").read_bytes() == loop_html(NOTES, FOLLOW_UP)
    assert (env.root / "out" / DAY / "derived" / f"{STEM} composed.html").is_file()
    assert "--print-to-pdf=" in log.read_text()


def test_given_configured_headings_when_land_composes_then_the_section_uses_them(
    env, tmp_path
):
    env.configure(summary_heading="Copilot Recap", anchor_heading="Action items")
    page = loop_html(NOTES, '<h1 style="m"><span>Action items</span></h1><p>x</p>')
    arrange_land(env, page)
    chromium, _ = fake_chromium(tmp_path)
    rc = run_land(
        env, "--chromium", str(chromium), "--ai-summary", str(summary_file(tmp_path))
    )
    composed = (env.data_dir() / f"{STEM}.pdf").read_bytes().decode()
    assert rc == 0
    assert composed.index(">Copilot Recap<") < composed.index(">Action items<")


def test_given_a_loop_page_without_the_ai_summary_and_no_summary_file_when_land_runs_then_only_a_notes_pdf_lands(
    env, capsys
):
    arrange_land(env, loop_html(NOTES, FOLLOW_UP))
    rc = run_land(env)
    data = env.data_dir()
    assert rc == env.mod.EXIT_FAIL
    assert not (data / f"{STEM}.pdf").exists()
    assert (data / f"{STEM} (no AI Summary).pdf").read_bytes() == GRAPH_PDF
    assert "AI Summary: MISSING" in capsys.readouterr().out


def test_given_a_loop_page_with_no_anchor_heading_when_land_composes_then_it_writes_no_deliverable(
    env, tmp_path, capsys
):
    arrange_land(env, loop_html(NOTES))
    chromium, log = fake_chromium(tmp_path)
    rc = run_land(
        env, "--chromium", str(chromium), "--ai-summary", str(summary_file(tmp_path))
    )
    assert rc == env.mod.EXIT_FAIL
    assert not (env.data_dir() / f"{STEM}.pdf").exists()
    assert not log.exists()
    assert "no Follow-up tasks heading" in capsys.readouterr().out


def test_given_chromium_exits_cleanly_but_writes_no_pdf_when_land_composes_then_it_reports_the_failure(
    env, tmp_path, capsys
):
    arrange_land(env, loop_html(NOTES, FOLLOW_UP))
    silent = tmp_path / "silent-chromium"
    silent.write_text("#!/bin/sh\nexit 0\n")
    silent.chmod(0o755)
    rc = run_land(
        env, "--chromium", str(silent), "--ai-summary", str(summary_file(tmp_path))
    )
    assert rc == env.mod.EXIT_FAIL
    assert not (env.data_dir() / f"{STEM}.pdf").exists()
    assert "notes pdf: FAILED chromium wrote no PDF" in capsys.readouterr().out


def test_given_graph_refuses_the_recording_when_land_runs_then_it_is_reported_and_no_partial_file_is_left(
    env, capsys
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    env.http.routes[f"/items/{REC_ITEM}/content"] = Resp(
        403, b"", {"error": {"code": "accessDenied"}}
    )
    rc = run_land(env)
    data = env.data_dir()
    assert rc == env.mod.EXIT_FAIL
    assert "recording: FAILED 403 accessDenied" in capsys.readouterr().out
    assert [p.name for p in data.iterdir() if p.name.startswith(REC_NAME)] == []
    assert (data / f"{STEM}.vtt").read_bytes() == UI_VTT


def test_given_a_recording_size_mismatch_when_land_runs_then_no_recording_is_kept(
    env, capsys
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    env.http.routes[f"/drives/{REC_DRIVE}/items/{REC_ITEM}?"] = Resp(
        200, b"", {"name": REC_NAME, "size": len(MP4_BODY) + 1}
    )
    rc = run_land(env)
    assert rc == env.mod.EXIT_FAIL
    assert [
        p.name for p in env.data_dir().iterdir() if p.name.startswith(REC_NAME)
    ] == []
    assert "driveItem says" in capsys.readouterr().out


def test_given_a_recording_path_git_would_track_when_land_runs_then_the_recording_is_not_downloaded(
    env, tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([shutil.which("git"), "init", "-q", str(repo)], timeout=60, check=True)
    env.configure(out_root=str(repo))
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    rc = run_land(env)
    assert rc == env.mod.EXIT_FAIL
    assert not (repo / DAY / "data" / REC_NAME).exists()
    assert not any(f"/items/{REC_ITEM}/content" in url for url in env.http.urls())
    assert "not ignored by git" in capsys.readouterr().out


def test_given_every_artefact_already_exists_when_land_reruns_without_force_then_nothing_is_fetched_again(
    env, capsys
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    assert run_land(env) == 0
    before = len(env.http.calls)
    capsys.readouterr()
    assert run_land(env) == 0
    fetched = [
        c["url"] for c in env.http.calls[before:] if "/conversations/" not in c["url"]
    ]
    assert fetched == []
    assert "kept existing" in capsys.readouterr().out


def test_given_an_out_root_flag_when_land_runs_then_it_overrides_the_config(
    env, tmp_path
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    target = tmp_path / "elsewhere"
    assert run_land(env, "--out-root", str(target)) == 0
    assert (target / DAY / "data" / f"{STEM}.vtt").read_bytes() == UI_VTT
    assert not env.data_dir().exists()


# ---------------------------------------------------------------------------- no token leaks


def echo_everywhere(e) -> None:
    """Every route answers with a body and an error code that echo a token."""
    echo = " ".join(ALL_TOKENS)
    for fragment in list(e.http.routes):
        e.http.routes[fragment] = Resp(
            401, f"bad bearer {echo}", {"error": {"code": echo}}
        )
    e.http.routes["/shares/"] = Resp(403, b"", {"error": {"code": f"denied {echo}"}})


def echo_in_thread(e) -> None:
    echo = " ".join(ALL_TOKENS)
    msg = {**THREAD[0], "content": f"<p>{echo}</p>", "imdisplayname": echo}
    e.http.routes["/conversations/19%3A"] = Resp(
        200, b"", {"messages": [msg, *THREAD[1:]]}
    )
    e.http.routes["/conversations?pageSize="] = Resp(
        200, b"", {"conversations": [{"id": CONV, "threadProperties": {"topic": echo}}]}
    )


def as_arranged(_e) -> None:
    """The routes as arrange_land left them."""


def http_raises(e) -> None:
    def boom(*_args, **_kwargs):
        raise OSError(f"connection reset while sending {ALL_TOKENS[0]}")

    e.http.get = boom
    e.http.post_json = boom
    e.http.download = boom


@pytest.mark.parametrize(
    "arrange", [echo_everywhere, echo_in_thread, http_raises, as_arranged]
)
@pytest.mark.parametrize(
    "argv",
    [
        ["chats", "--query", "Team"],
        ["chats", "--query", "Team", "--json"],
        ["instances", "--conv", CONV, "--json"],
        ["messages", "--conv", CONV],
        ["thread", "--conv", CONV, "--json"],
        ["vtt", "--conv", CONV, "--date", DAY, "--out", "OUT"],
        ["loop-links", "--conv", CONV, "--date", DAY],
        ["recording-uris", "--conv", CONV, "--date", DAY],
        ["land", "--conv", CONV, "--date", DAY],
        ["land", "--conv", CONV, "--date", DAY, "--time", "10:00"],
        ["send", "--to-notes", "--file", "BODY", "--send", "--sha256", "SHA"],
    ],
    ids=lambda a: a[0] if isinstance(a, list) else None,
)
def test_given_any_command_when_it_runs_then_no_token_value_reaches_stdout_or_stderr(
    env, tmp_path, capsys, argv, arrange
):
    f, digest = body_file(tmp_path)
    argv = [
        {"OUT": str(tmp_path), "BODY": str(f), "SHA": digest}.get(a, a) for a in argv
    ]
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    arrange(env)
    env.mod.main(argv)
    captured = capsys.readouterr()
    for token in ALL_TOKENS:
        assert token not in captured.out
        assert token not in captured.err


# ---------------------------------------------------------------------------- chats --json, instances, land --time

REC_ITEM_LATE = "01FAKERECITEMSECOND00000000000"
REC_NAME_LATE = "Team Stand-up-20261019_180100UTC-Meeting Recording.mp4"
OBJ_LATE = "0-eus-d3-late000000000000000000000"
TID_LATE = "fake-transcript-late"
LATE_VTT = b"WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\n<v Sam Sample>Second meeting.</v>\r\n"
LATE_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x01" * 2048
LATE_INNER = EXAMPLE_INNER.replace("FAKEFILEID", "FAKEFILEIDLATE")
LATE_LOOP = "https://loop.cloud.microsoft/p/" + _b64url(
    json.dumps({"p": {"u": LATE_INNER, "r": True}})
)


REC_ITEM_YESTERDAY = "01FAKERECITEMYESTERDAY00000000"
REC_NAME_YESTERDAY = "Team Stand-up-20261018_140100UTC-Meeting Recording.mp4"


def renamed_recording(rec: dict, item: str, name: str) -> dict:
    rec["content"] = rec["content"].replace(REC_ITEM, item).replace(REC_NAME, name)
    return rec


def late_recording() -> dict:
    rec = recording(OBJ_LATE, "2026-10-19T19:00:00.000Z", "rec-late", TID_LATE)
    return renamed_recording(rec, REC_ITEM_LATE, REC_NAME_LATE)


def loop_message(mid: str, when: str, link: str) -> dict:
    return {
        "id": mid,
        "originalarrivaltime": when,
        "messagetype": "RichText/Html",
        "imdisplayname": "Facilitator",
        "content": f'<p>Notes: <a href="{link}">Loop</a></p>',
    }


TWO_A_DAY = [
    late_recording(),
    loop_message("loop-late", "2026-10-19T17:55:00.000Z", LATE_LOOP),
    recording(OBJ_TODAY, "2026-10-19T15:00:00.000Z", "rec-early", TID_TODAY),
    loop_message("loop-early", "2026-10-19T13:55:00.000Z", EXAMPLE_LOOP),
    renamed_recording(
        recording(
            OBJ_YESTERDAY, "2026-10-18T14:05:00.000Z", "rec-yesterday", TID_YESTERDAY
        ),
        REC_ITEM_YESTERDAY,
        REC_NAME_YESTERDAY,
    ),
]


def arrange_two_a_day(env) -> None:
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    env.http.routes["/conversations/19%3A"] = Resp(200, b"", {"messages": TWO_A_DAY})
    env.http.routes[f"/transcripts/{TID_LATE}{ODSP_QUERY}"] = Resp(200, BOM + LATE_VTT)
    env.http.routes[f"/items/{REC_ITEM_LATE}/content"] = Resp(200, LATE_MP4)
    env.http.routes[f"/drives/{REC_DRIVE}/items/{REC_ITEM_LATE}?"] = Resp(
        200, b"", {"name": REC_NAME_LATE, "size": len(LATE_MP4)}
    )


def test_given_chats_when_chats_runs_with_json_then_it_prints_conv_and_topic_objects(
    env, capsys
):
    rc = env.mod.main(["chats", "--query", "stand-up", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [
        {"conv": CONV, "topic": "Team Stand-up"}
    ]


def test_given_no_matching_chat_when_chats_runs_with_json_then_it_prints_an_empty_list(
    env, capsys
):
    assert env.mod.main(["chats", "--query", "no such topic", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_given_two_recordings_a_day_when_instances_runs_then_each_lists_newest_first_with_local_start(
    env, capsys
):
    arrange_two_a_day(env)
    rc = env.mod.main(["instances", "--conv", CONV, "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "date": DAY,
            "time": "14:01",
            "start_utc": "2026-10-19T18:01:00Z",
            "recording": REC_NAME_LATE,
            "message_id": "rec-late",
        },
        {
            "date": DAY,
            "time": "10:01",
            "start_utc": "2026-10-19T14:01:00Z",
            "recording": REC_NAME,
            "message_id": "rec-early",
        },
        {
            "date": "2026-10-18",
            "time": "10:01",
            "start_utc": "2026-10-18T14:01:00Z",
            "recording": REC_NAME_YESTERDAY,
            "message_id": "rec-yesterday",
        },
    ]


def test_given_a_date_when_instances_runs_then_only_that_local_date_is_listed(
    env, capsys
):
    arrange_two_a_day(env)
    env.mod.main(["instances", "--conv", CONV, "--date", "2026-10-18", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert [r["message_id"] for r in rows] == ["rec-yesterday"]


def test_given_a_recording_name_without_a_start_stamp_when_instances_runs_then_the_message_time_is_used(
    env, capsys
):
    rec = recording(OBJ_TODAY, "2026-10-19T15:30:00.000Z", "rec-plain", TID_TODAY)
    rec["content"] = rec["content"].replace(REC_NAME, "Meeting Recording.mp4")
    env.http.routes["/conversations/19%3A"] = Resp(200, b"", {"messages": [rec]})
    env.mod.main(["instances", "--conv", CONV, "--json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert (row["time"], row["start_utc"]) == ("11:30", "2026-10-19T15:30:00Z")


def test_given_two_recordings_a_day_when_land_runs_without_time_then_it_refuses_listing_both(
    env, capsys
):
    arrange_two_a_day(env)
    rc = run_land(env)
    err = capsys.readouterr().err
    assert rc == env.mod.EXIT_REFUSED
    assert "10:01" in err and "14:01" in err
    assert REC_NAME in err and REC_NAME_LATE in err
    assert "--time" in err
    assert not env.data_dir().exists()
    assert [u for u in env.http.urls() if "/conversations/" not in u] == []


def test_given_two_recordings_a_day_when_land_runs_with_the_late_time_then_only_that_instance_lands(
    env, capsys
):
    arrange_two_a_day(env)
    rc = run_land(env, "--time", "14:00")
    data = env.data_dir()
    stem = f"{STEM} 1401"
    assert rc == 0, capsys.readouterr()
    assert (data / f"{stem}.vtt").read_bytes() == LATE_VTT
    assert (data / REC_NAME_LATE).read_bytes() == LATE_MP4
    assert not (data / REC_NAME).exists()
    assert (data / f"{stem}.pdf").read_bytes() == GRAPH_PDF
    urls = env.http.urls()
    assert not any(TID_TODAY in u or f"/items/{REC_ITEM}/" in u for u in urls)
    shares = [u for u in urls if "/shares/" in u]
    assert shares[0].endswith(f"/shares/{env.mod.share_id(LATE_INNER)}/driveItem")


def test_given_two_recordings_a_day_when_land_runs_with_the_early_time_then_the_early_loop_link_is_used(
    env,
):
    arrange_two_a_day(env)
    assert run_land(env, "--time", "10:30") == 0
    data = env.data_dir()
    assert (data / f"{STEM} 1001.vtt").read_bytes() == UI_VTT
    assert (data / REC_NAME).read_bytes() == MP4_BODY
    assert not (data / REC_NAME_LATE).exists()
    shares = [u for u in env.http.urls() if "/shares/" in u]
    assert shares[0].endswith(f"/shares/{env.mod.share_id(EXAMPLE_INNER)}/driveItem")


def test_given_two_recordings_a_day_when_land_time_matches_neither_then_it_refuses_listing_them(
    env, capsys
):
    arrange_two_a_day(env)
    rc = run_land(env, "--time", "12:00")
    err = capsys.readouterr().err
    assert rc == env.mod.EXIT_REFUSED
    assert "10:01" in err and "14:01" in err
    assert not env.data_dir().exists()


def test_given_one_recording_that_day_when_land_runs_with_time_then_the_stem_carries_no_time(
    env,
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    assert run_land(env, "--time", "10:00") == 0
    assert (env.data_dir() / f"{STEM}.vtt").read_bytes() == UI_VTT


def test_given_a_malformed_time_when_land_runs_then_it_refuses(env):
    assert run_land(env, "--time", "9am") == env.mod.EXIT_REFUSED


def test_given_the_onedrive_transcript_refuses_when_land_runs_then_the_gateway_view_lands_with_a_warning(
    env, capsys
):
    """Parity with vtt: the OneDrive file first, then the gateway view with its warning."""
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    env.http.routes[f"/transcripts/{TID_TODAY}{ODSP_QUERY}"] = Resp(
        403, b"", {"error": {"code": "accessDenied"}}
    )
    rc = run_land(env)
    out = capsys.readouterr()
    assert rc == 0
    assert (env.data_dir() / f"{STEM}.vtt").read_bytes() == GATEWAY_VTT
    assert "OneDrive transcript: 403 accessDenied" in out.err
    assert "merges each speaker's consecutive cues" in out.err
    transcript_urls = [u for u in env.http.urls() if "transcript" in u]
    assert ODSP_QUERY in transcript_urls[0]
    assert "/views/transcript" in transcript_urls[-1]


def test_given_every_artefact_already_exists_when_land_reruns_with_force_then_each_is_fetched_again(
    env,
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    assert run_land(env) == 0
    (env.data_dir() / f"{STEM}.vtt").write_bytes(b"stale")
    before = len(env.http.calls)
    assert run_land(env, "--force") == 0
    fetched = [c["url"] for c in env.http.calls[before:]]
    assert any(TID_TODAY in u for u in fetched)
    assert any(f"/items/{REC_ITEM}/content" in u for u in fetched)
    assert any("format=pdf" in u for u in fetched)
    assert (env.data_dir() / f"{STEM}.vtt").read_bytes() == UI_VTT


def test_given_the_land_command_when_standup_is_asked_for_then_argparse_rejects_it(
    env,
):
    with pytest.raises(SystemExit) as exc:
        env.mod.main(["standup", "--conv", CONV, "--date", DAY])
    assert exc.value.code == 2  # noqa: PLR2004


def progress_event(mid: str, when: str, status: str, code: str) -> dict:
    """A recording progress message Teams posts while a recording runs: a status, no file."""
    content = (
        '<URIObject type="Video.2/CallRecording">'
        f'<RecordingStatus status="{status}" code="{code}" shortChunk="False">'
        '</RecordingStatus><OriginalName v=""></OriginalName></URIObject>'
    )
    return {
        "id": mid,
        "originalarrivaltime": when,
        "messagetype": "RichText/Media_CallRecording",
        "content": content,
    }


ONE_MEETING_WITH_PROGRESS = [
    # newest first: the finished recording arrives after its chunk events
    recording(OBJ_TODAY, "2026-10-19T14:45:00.000Z", "rec-today", TID_TODAY),
    progress_event("chunk-2", "2026-10-19T14:44:02.714Z", "ChunkFinished", "202"),
    progress_event("chunk-1", "2026-10-19T14:44:02.533Z", "ChunkFinished", "202"),
    THREAD[1],
    progress_event("start-2", "2026-10-19T14:01:01.206Z", "Initial", "0"),
    progress_event("start-1", "2026-10-19T14:01:00.489Z", "Initial", "0"),
]


def test_given_recording_progress_messages_when_instances_runs_then_only_the_finished_recording_is_an_instance(
    env, capsys
):
    env.http.routes["/conversations/19%3A"] = Resp(
        200, b"", {"messages": ONE_MEETING_WITH_PROGRESS}
    )
    assert env.mod.main(["instances", "--conv", CONV, "--date", DAY, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["time"], r["recording"], r["message_id"]) for r in rows] == [
        ("10:01", REC_NAME, "rec-today")
    ]


def test_given_recording_progress_messages_when_land_runs_without_time_then_the_one_meeting_lands(
    env, capsys
):
    arrange_land(env, loop_html(NOTES, AI_DONE, FOLLOW_UP))
    env.http.routes["/conversations/19%3A"] = Resp(
        200, b"", {"messages": ONE_MEETING_WITH_PROGRESS}
    )
    assert run_land(env) == 0, capsys.readouterr()
    data = env.data_dir()
    assert (data / f"{STEM}.vtt").read_bytes() == UI_VTT
    assert (data / REC_NAME).read_bytes() == MP4_BODY


def test_given_a_recording_message_with_no_status_when_instances_runs_then_it_still_counts(
    env, capsys
):
    rec = recording(OBJ_TODAY, "2026-10-19T14:05:00.000Z", "rec-today", TID_TODAY)
    rec["content"] = rec["content"].replace(
        '<RecordingStatus status="Success" code="200" />', ""
    )
    assert "RecordingStatus" not in rec["content"]
    env.http.routes["/conversations/19%3A"] = Resp(200, b"", {"messages": [rec]})
    assert env.mod.main(["instances", "--conv", CONV, "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert (row["recording"], row["message_id"]) == (REC_NAME, "rec-today")
