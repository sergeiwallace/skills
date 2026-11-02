#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Read Microsoft Teams chats and meeting artefacts, and send one human-approved message.

Tokens come from a pluggable provider chosen in the settings TOML (read at call time):
the built-in "json-file" provider reads a JSON file a token broker wrote, and any other
value is "<module>:<callable>", a broker adapter. Every token value the provider returns
is registered with the output writer before anything is printed, so no command can print
one, error paths included.

Standard library only. `--help` and a send preview never load a token.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import html
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = Path("~/.config/teams-skill/teams.toml").expanduser()
RECAP_JS = Path(__file__).resolve().parent / "recap_summary.js"
ENV_CONFIG = "TEAMS_CONFIG"
ENV_PROFILE = "TEAMS_PROFILE"

DEFAULTS: dict = {
    "provider": "json-file",
    "provider_path": "",
    "login_hint": "Refresh the token file: run your token broker's sign-in, then retry.",
    "region": "",
    "gateway_region": "us",
    "timezone": "UTC",
    "title": "Meeting",
    "out_root": "meetings",
    "summary_heading": "AI Summary",
    "anchor_heading": "Follow-up tasks",
    "chromium": "",
    "sharepoint_hosts": [],
    "cdp_port": 0,
    "recap_tab_hosts": ["teams.cloud.microsoft"],
    # With no Teams tab open, recap-summary opens this URL in a new tab and, when it is done,
    # leaves that tab on recap_park_url instead of closing it.
    "recap_open_url": "https://teams.cloud.microsoft/",
    "recap_park_url": "chrome://newtab/",
    "chat_service_url": "https://{region}.ng.msg.teams.microsoft.com/v1/users/ME/conversations",
    "gateway_url": "https://{gateway_region}-prod.asyncgw.teams.microsoft.com/v1/objects/{oid}/views/transcript",
    "graph_url": "https://graph.microsoft.com/v1.0",
    "json-file": {"path": "~/.config/teams-skill/tokens.json"},
}

TOKEN_FIELDS = ("chat_service", "ic3", "graph", "sharepoint")
MIN_SECRET_LEN = 8
MAX_PAGE = 200
EXCERPT = 160
RECORDING_TYPE = "RichText/Media_CallRecording"
# The UTC start a Teams recording's OneDrive file name carries: "...-20261019_140100UTC-...".
RECORDING_START = re.compile(r"-(\d{8})_(\d{6})UTC-")
RECORDING_STATUS = re.compile(r'<RecordingStatus\b[^>]*\bstatus="([^"]*)"')
# How far --time may sit from an instance's local start and still pick it, in minutes.
TIME_WINDOW_MIN = 45
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
GATEWAY_OBJECT = re.compile(r"asyncgw[^\"'\s]*?/objects/([^/\"'\s]+)/views/")
# Appended to a recording message's onedriveForBusinessTranscript URL in place of its trailing
# /content. With the UTF-8 BOM stripped, this is the file the client's "Download as .vtt" gives.
ODSP_TRANSCRIPT_VIEW = "/streamContent?is=1&applymediaedits=false"
UTF8_BOM = b"\xef\xbb\xbf"
CHROMIUM_GLOB = "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
UL_OPEN = '<ul style="margin-top: 0px; margin-bottom: 8px;">'
NOTES_CONV = "48:notes"
HTTP_AUTH_FAIL = (401, 403)
HTTP_UNAUTHORIZED = 401
# recap-summary: seconds between two reads of the tab, how many reads one wait may take, how
# many a freshly opened Teams tab may take to load, and the bounds on scrolling for the meeting
# card and on expansion passes.
RECAP_POLL_S = 1.0
RECAP_WAIT_POLLS = 30
RECAP_OPEN_POLLS = 90
RECAP_SCROLL_STEPS = 40
RECAP_STILL_STEPS = 3
RECAP_EXPAND_PASSES = 10
RECAP_EVAL_TIMEOUT_S = 30.0
# Every CDP method recap-summary may send; anything else is refused before it leaves the process.
# It brings the Teams tab to the front (a tab that is not in front paints no frames, so a chat
# switch never finishes there), or opens one and later navigates that tab only to
# recap_park_url. It never closes a target or sends input events.
RECAP_METHODS = frozenset(
    {
        "Target.attachToTarget",
        "Target.detachFromTarget",
        "Target.activateTarget",
        "Target.createTarget",
        "Page.navigate",
        "Runtime.evaluate",
        "Network.enable",
        "Network.disable",
        "Network.getResponseBody",
    }
)
COLLAB_PATH_MARK = "/readcollabobject/"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_CONFIG, EXIT_AUTH = 0, 1, 2, 3, 4


class ConfigError(RuntimeError):
    pass


class AuthError(RuntimeError):
    pass


class Refusal(RuntimeError):
    pass


# ---------------------------------------------------------------------------- output


class _Out:
    """All output goes through here so a token value can never reach stdout or stderr."""

    def __init__(self) -> None:
        self.secrets: list[str] = []

    def protect(self, values) -> None:
        for value in values:
            if value and len(value) >= MIN_SECRET_LEN and value not in self.secrets:
                self.secrets.append(value)
        self.secrets.sort(key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        return text

    def out(self, text: str = "") -> None:
        sys.stdout.write(self.redact(text) + "\n")

    def err(self, text: str) -> None:
        sys.stderr.write(self.redact(text) + "\n")


OUT = _Out()


# ---------------------------------------------------------------------------- HTTP


@dataclass
class Response:
    status_code: int
    content: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300  # noqa: PLR2004

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class _StripAuthOnHostChange(urllib.request.HTTPRedirectHandler):
    """Drop credentials when a redirect leaves the host, as a browser does.

    Graph answers a drive item /content request with a redirect to a pre-authenticated
    download URL on another host, which must not receive the Graph bearer.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913, PLR0917
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
            for name in ("Authorization", "Authentication"):
                new.headers.pop(name, None)
        return new


class UrllibHttp:
    """The only network surface. Tests replace the module's HTTP with a fake."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_StripAuthOnHostChange())

    def _open(self, req: urllib.request.Request, timeout: float):
        try:
            return self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            return exc

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        with self._open(
            urllib.request.Request(url, headers=dict(headers)), timeout
        ) as r:
            return Response(r.getcode(), r.read())

    def post_json(
        self, url: str, headers: Mapping[str, str], payload: dict, timeout: float
    ) -> Response:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with self._open(req, timeout) as r:
            return Response(r.getcode(), r.read())

    def download(
        self, url: str, headers: Mapping[str, str], dest: Path, timeout: float
    ) -> Response:
        """Stream a 2xx body into `dest`; any other status returns its body unwritten."""
        with self._open(
            urllib.request.Request(url, headers=dict(headers)), timeout
        ) as r:
            status = r.getcode()
            if not 200 <= status < 300:  # noqa: PLR2004
                return Response(status, r.read())
            with open(dest, "wb") as fh:
                shutil.copyfileobj(r, fh, 1 << 20)
            return Response(status, b"")


HTTP = UrllibHttp()


# ---------------------------------------------------------------------------- config


def _merge(base: dict, over: Mapping) -> dict:
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(flag: str | None) -> dict:
    """[defaults] then [profiles.<TEAMS_PROFILE>] from the config file, over the code defaults."""
    explicit = flag or os.environ.get(ENV_CONFIG)
    path = (
        Path(os.path.expandvars(os.path.expanduser(explicit)))
        if explicit
        else DEFAULT_CONFIG_PATH
    )
    if not path.is_file():
        if explicit:
            raise ConfigError(f"config file not found: {path}")
        return dict(DEFAULTS)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
    settings = _merge(DEFAULTS, data.get("defaults", {}))
    profile = os.environ.get(ENV_PROFILE, "")
    if profile:
        settings = _merge(settings, data.get("profiles", {}).get(profile, {}))
    return settings


# ---------------------------------------------------------------------------- token provider


@dataclass(frozen=True)
class TeamsTokens:
    """The four Teams web-client tokens and the chat-service region. Empty means not held."""

    chat_service: str
    ic3: str
    graph: str
    sharepoint: str
    region: str

    def need(self, field: str) -> str:
        value = getattr(self, field)
        if not value:
            raise AuthError(f"the token provider returned no {field} token")
        return value


def json_file_provider(settings: Mapping) -> Mapping:
    """Read the token file a broker wrote: {"chat_service": ..., "ic3": ..., "graph": ...,
    "sharepoint": ..., "region": ...}. A missing or unreadable file is not authenticated."""
    raw = str((settings.get("json-file") or {}).get("path", ""))
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AuthError(f"no token file at {path}") from None
    except (OSError, ValueError) as exc:
        raise AuthError(
            f"token file {path} is unreadable: {type(exc).__name__}"
        ) from None
    if not isinstance(data, Mapping):
        raise AuthError(f"token file {path} is not a JSON object")
    return data


def resolve_provider(settings: Mapping) -> Callable[[Mapping], Mapping | TeamsTokens]:
    name = str(settings.get("provider", ""))
    if name == "json-file":
        return json_file_provider
    module, _, attr = name.partition(":")
    if not module or not attr:
        raise ConfigError(
            f"provider must be 'json-file' or '<module>:<callable>', not {name!r}"
        )
    extra = str(settings.get("provider_path", ""))
    if extra:
        sys.path.insert(0, os.path.expandvars(os.path.expanduser(extra)))
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(
            f"cannot load provider {name!r}: {type(exc).__name__}: {exc}"
        ) from None


def load_tokens(settings: Mapping) -> TeamsTokens:
    provider = resolve_provider(settings)
    try:
        got = provider(settings)
    except (AuthError, ConfigError):
        raise
    except Exception as exc:  # noqa: BLE001  (a broker failure is reported as not authenticated)
        raise AuthError(f"token provider failed: {type(exc).__name__}: {exc}") from None
    fields = got.__dict__ if isinstance(got, TeamsTokens) else dict(got)
    values = {f: str(fields.get(f) or "") for f in TOKEN_FIELDS}
    OUT.protect(values.values())
    region = str(fields.get("region") or settings.get("region") or "")
    if not region:
        raise ConfigError(
            "neither the token provider nor the config names a chat-service region"
        )
    return TeamsTokens(region=region, **values)


@dataclass
class Ctx:
    settings: dict
    tokens: TeamsTokens

    @property
    def hint(self) -> str:
        return str(self.settings["login_hint"])

    def chat_base(self) -> str:
        return self.settings["chat_service_url"].format(region=self.tokens.region)

    def chat_headers(self) -> dict:
        return {"Authentication": f"skypetoken={self.tokens.need('chat_service')}"}

    def bearer(self, field: str) -> dict:
        return {"Authorization": f"Bearer {self.tokens.need(field)}"}

    def graph(self, path: str) -> Response:
        return HTTP.get(self.settings["graph_url"] + path, self.bearer("graph"), 30)


# ---------------------------------------------------------------------------- pure helpers


def parse_utc(stamp: str) -> datetime | None:
    """A Teams arrival time ('2026-10-19T14:05:07.123Z') as aware UTC."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.rstrip("Z")[:19]).replace(tzinfo=UTC)
    except ValueError:
        return None


def local_time(stamp: str, tz: str) -> str:
    when = parse_utc(stamp)
    return (
        when.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S %Z") if when else "?"
    )


def on_local_date(stamp: str, day: str, tz: str) -> bool:
    when = parse_utc(stamp)
    return when is not None and when.astimezone(ZoneInfo(tz)).date().isoformat() == day


def plain_text(content: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", content or "")).split())


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def share_id(url: str) -> str:
    """Graph /shares id for a sharing URL."""
    return "u!" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _nav_ids(result: dict) -> None:
    nav = parse_qs(urlsplit(result["inner_url"]).query).get("nav", [""])[0]
    result["nav"] = nav
    if not nav:
        return
    try:
        fields = parse_qs(b64url_decode(nav).decode())
    except ValueError as exc:
        result["error"] = f"undecodable nav: {type(exc).__name__}"
        return
    result["drive_id"] = fields.get("d", [""])[0]
    result["item_id"] = fields.get("f", [""])[0]


def decode_loop_link(url: str) -> dict:
    """Unwrap loop.cloud.microsoft/p/<base64url json> to its inner :fl: URL and the nav ids."""
    result = {
        "link": url,
        "inner_url": "",
        "nav": "",
        "drive_id": "",
        "item_id": "",
        "error": "",
    }
    parts = urlsplit(url)
    if parts.netloc.endswith("loop.cloud.microsoft") and parts.path.startswith("/p/"):
        try:
            result["inner_url"] = json.loads(
                b64url_decode(parts.path[3:].split("/")[0])
            )["p"]["u"]
        except (ValueError, KeyError, TypeError) as exc:
            result["error"] = f"undecodable loop wrapper: {type(exc).__name__}"
            return result
    elif ":fl:" in url:
        result["inner_url"] = url
    else:
        result["error"] = "not a Loop link"
        return result
    _nav_ids(result)
    return result


def classify_host(url: str, sharepoint_hosts) -> str:
    host = urlsplit(url).netloc.lower()
    if "asyncgw" in host:
        return "async media gateway (IC3 token)"
    if host.endswith(".sharepoint.com") or host in {
        h.lower() for h in sharepoint_hosts
    }:
        return "sharepoint/onedrive (Graph /shares or the SharePoint token)"
    if host.endswith("asm.skype.com"):
        return "chat-service media (chat-service token)"
    return f"other ({host or 'no host'})"


def loop_hrefs(content: str) -> list[str]:
    found = re.findall(r"https://[^\"'\s<>]+", html.unescape(content or ""))
    return [u for u in found if "loop.cloud.microsoft" in u or ":fl:" in u]


def graph_error_code(resp: Response) -> str:
    try:
        return str((resp.json().get("error") or {}).get("code", ""))
    except (ValueError, AttributeError):
        return ""


def check_title(title: str) -> None:
    if "/" in title or title in {"", ".", ".."}:
        raise Refusal(f"--title must be a plain file name stem: {title!r}")


def write_bytes(target: Path, data: bytes, force: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb" if force else "xb") as fh:
        fh.write(data)


# ---------------------------------------------------------------------------- chat-service reads


def chat_get(ctx: Ctx, url: str) -> dict:
    resp = HTTP.get(url, ctx.chat_headers(), 30)
    if resp.status_code in HTTP_AUTH_FAIL:
        raise AuthError(
            f"the chat service returned {resp.status_code} (token expired, or not a member)"
        )
    if not resp.ok:
        raise RuntimeError(f"chat service error {resp.status_code}")
    return resp.json()


def fetch_messages(ctx: Ctx, conv: str, limit: int) -> list[dict]:
    """One page (at most 200) of raw message dicts, newest first, markup intact."""
    url = f"{ctx.chat_base()}/{quote(conv, safe='')}/messages?pageSize={min(int(limit), MAX_PAGE)}"
    return chat_get(ctx, url).get("messages", []) or []


def warn_window(msgs: list[dict], day: str, tz: str, kept: list[dict]) -> None:
    times = [
        t for t in (parse_utc(m.get("originalarrivaltime", "")) for m in msgs) if t
    ]
    if (
        not kept
        and times
        and min(times).astimezone(ZoneInfo(tz)).date().isoformat() > day
    ):
        OUT.err(
            f"warning: the oldest fetched message is after {day}; one page holds at most "
            f"{MAX_PAGE}, so that date may be older than the fetched window"
        )


def messages_on(
    ctx: Ctx, conv: str, day: str, tz: str, limit: int = MAX_PAGE
) -> list[dict]:
    msgs = fetch_messages(ctx, conv, limit)
    kept = [m for m in msgs if on_local_date(m.get("originalarrivaltime", ""), day, tz)]
    warn_window(msgs, day, tz, kept)
    return kept


def chat_kind(conv_id: str) -> str:
    if "meeting" in conv_id:
        return "meeting"
    return "group" if "@thread" in conv_id else "oneOnOne"


def chat_topic(chat: Mapping) -> str:
    props = chat.get("threadProperties") or {}
    return (
        props.get("topic")
        or props.get("spaceThreadTopic")
        or props.get("topicThreadTopic")
        or ""
    )


def cmd_chats(ctx: Ctx, args) -> int:
    size = MAX_PAGE if args.query else min(args.limit, MAX_PAGE)
    convs = (
        chat_get(ctx, f"{ctx.chat_base()}?pageSize={size}").get("conversations", [])
        or []
    )
    query = (args.query or "").lower()
    rows: list[dict] = []
    for chat in convs:
        cid, topic = str(chat.get("id", "")), chat_topic(chat)
        if query and query not in topic.lower():
            continue
        rows.append({"conv": cid, "topic": topic})
        if len(rows) >= args.limit:
            break
    OUT.err(f"{len(rows)} chat(s) of {len(convs)} fetched")
    if args.json:
        OUT.out(json.dumps(rows, indent=2))
        return EXIT_OK
    for row in rows:
        topic = row["topic"] or "(no topic)"
        OUT.out(f"{row['conv']}\t{chat_kind(row['conv'])}\t{topic}")
    return EXIT_OK if rows else EXIT_FAIL


def cmd_messages(ctx: Ctx, args) -> int:
    msgs = fetch_messages(ctx, args.conv, args.limit)
    for m in msgs:
        when = local_time(m.get("originalarrivaltime", ""), args.tz)
        OUT.out(
            f"[{when}] {m.get('imdisplayname', '') or '?'}: {plain_text(m.get('content', ''))}"
        )
    OUT.err(f"{len(msgs)} message(s), newest first")
    return EXIT_OK


def cmd_thread(ctx: Ctx, args) -> int:
    msgs = (
        messages_on(ctx, args.conv, args.date, args.tz, args.limit)
        if args.date
        else (fetch_messages(ctx, args.conv, args.limit))
    )
    rows = [
        {
            "id": str(m.get("id", "")),
            "ts": m.get("originalarrivaltime", ""),
            "sender": m.get("imdisplayname", "") or "",
            "type": m.get("messagetype", ""),
            "text": plain_text(m.get("content", "")),
        }
        for m in msgs
    ]
    if args.json:
        OUT.out(json.dumps(rows, indent=2))
        return EXIT_OK
    for r in rows:
        when = local_time(r["ts"], args.tz)
        OUT.out(f"{r['id']}\t{when}\t{r['sender']}\t{r['type']}\t{r['text'][:EXCERPT]}")
    OUT.err(f"{len(rows)} message(s)")
    return EXIT_OK


# ---------------------------------------------------------------------------- recordings and transcripts


def recordings(msgs: list[dict]) -> list[dict]:
    return [m for m in msgs if m.get("messagetype") == RECORDING_TYPE]


def recording_objects(msgs: list[dict]) -> list[str]:
    ids: list[str] = []
    for m in recordings(msgs):
        ids += GATEWAY_OBJECT.findall(m.get("content", "") or "")
    return list(dict.fromkeys(ids))


def _items(msgs: list[dict], kind: str) -> list[tuple[dict, str]]:
    """(attributes, unescaped content) of every <item type="kind"> in the recording messages."""
    found = []
    for m in recordings(msgs):
        content = html.unescape(m.get("content", "") or "")
        for tag in re.findall(rf'<item type="{kind}"[^>]*>', content):
            found.append((dict(re.findall(r'(\w+)="([^"]*)"', tag)), content))
    return found


def transcript_urls(msgs: list[dict]) -> list[tuple[str, str]]:
    """(transcript id, OneDrive transcript view URL) per onedriveForBusinessTranscript item."""
    found: dict[str, str] = {}
    for attrs, _ in _items(msgs, "onedriveForBusinessTranscript"):
        uri = attrs.get("uri", "")
        if uri.endswith("/content") and uri not in found:
            found[uri] = attrs.get("id", "")
    return [
        (tid, uri[: -len("/content")] + ODSP_TRANSCRIPT_VIEW)
        for uri, tid in found.items()
    ]


def _onedrive_transcript(ctx: Ctx, msgs: list[dict]) -> tuple[bytes, str]:
    urls = transcript_urls(msgs)
    if urls and not ctx.tokens.sharepoint:
        OUT.err(
            "OneDrive transcript: skipped, the token provider returned no sharepoint token"
        )
        return b"", ""
    for tid, url in urls:
        try:
            r = HTTP.get(url, ctx.bearer("sharepoint"), 60)
        except OSError as exc:
            OUT.err(f"OneDrive transcript {tid}: fetch failed: {type(exc).__name__}")
            continue
        body = r.content.removeprefix(UTF8_BOM)
        if r.ok and body.startswith(b"WEBVTT"):
            return body, f"OneDrive transcript {tid}"
        OUT.err(
            f"OneDrive transcript: {r.status_code} {graph_error_code(r)}, no WEBVTT"
        )
    return b"", ""


def _gateway_transcript(ctx: Ctx, msgs: list[dict]) -> tuple[bytes, str]:
    headers = ctx.bearer("ic3")
    for oid in recording_objects(msgs):
        url = ctx.settings["gateway_url"].format(
            gateway_region=ctx.settings["gateway_region"], oid=oid
        )
        try:
            r = HTTP.get(url, headers, 45)
        except OSError as exc:
            OUT.err(f"object {oid}: fetch failed: {type(exc).__name__}")
            continue
        if r.ok and b"WEBVTT" in r.content[:200]:
            OUT.err(
                "warning: this is the gateway's transcript view, which merges each speaker's "
                "consecutive cues; it is not byte-identical to the client's 'Download as .vtt' file"
            )
            return r.content, f"gateway object {oid}"
        OUT.err(f"object {oid}: gateway {r.status_code}, no WEBVTT")
        if r.status_code == HTTP_UNAUTHORIZED:
            OUT.err(f"the IC3 token was refused (expired?). {ctx.hint}")
    return b"", ""


def fetch_transcript(ctx: Ctx, msgs: list[dict]) -> tuple[bytes, str]:
    """That day's transcript and its route: the OneDrive file first, then the gateway view."""
    body, source = _onedrive_transcript(ctx, msgs)
    return (body, source) if body else _gateway_transcript(ctx, msgs)


def has_recording(msgs: list[dict]) -> bool:
    return bool(transcript_urls(msgs) or recording_objects(msgs))


def cmd_vtt(ctx: Ctx, args) -> int:
    check_title(args.title)
    target = Path(args.out) / f"{args.title} {args.date}.vtt"
    if target.exists() and not args.force:
        raise Refusal(f"{target} already exists; pass --force to overwrite it")
    msgs = messages_on(ctx, args.conv, args.date, args.tz)
    if not has_recording(msgs):
        OUT.err(
            f"no {RECORDING_TYPE} object on {args.date} ({len(msgs)} message(s) that day)"
        )
        return EXIT_FAIL
    body, source = fetch_transcript(ctx, msgs)
    if not body:
        return EXIT_FAIL
    write_bytes(target, body, args.force)
    OUT.out(f"wrote {target} ({len(body)} bytes) from {source}")
    return EXIT_OK


def cmd_recording_uris(ctx: Ctx, args) -> int:
    msgs = recordings(messages_on(ctx, args.conv, args.date, args.tz))
    if not msgs:
        OUT.err(f"no {RECORDING_TYPE} message on {args.date}")
        return EXIT_FAIL
    hosts = ctx.settings["sharepoint_hosts"]
    for m in msgs:
        content = m.get("content", "") or ""
        OUT.out(f"message {m.get('id', '')} at {m.get('originalarrivaltime', '')}")
        for oid in dict.fromkeys(GATEWAY_OBJECT.findall(content)):
            OUT.out(f"  gateway object: {oid}")
        for uri in dict.fromkeys(
            html.unescape(u) for u in re.findall(r'uri="([^"]+)"', content) if u
        ):
            OUT.out(f"  uri: {uri}")
            OUT.out(f"    host: {classify_host(uri, hosts)}")
    return EXIT_OK


# ---------------------------------------------------------------------------- Loop links


def _print_decoded(info: dict) -> None:
    OUT.out(f"link: {info['link']}")
    if info["error"]:
        OUT.out(f"  decode: FAILED {info['error']}")
    if info["inner_url"]:
        OUT.out(f"  inner_url: {info['inner_url']}")
    if info["nav"]:
        OUT.out(f"  nav: {info['nav']}")
        OUT.out(f"  nav.d (driveId): {info['drive_id']}")
        OUT.out(f"  nav.f (itemId): {info['item_id']}")


def _resolve_candidates(info: dict) -> list[tuple[str, str]]:
    link, inner = info["link"], info["inner_url"]
    labelled = [("inner" if inner != link else "link", inner), ("link", link)]
    return list(dict.fromkeys((label, url) for label, url in labelled if url))


def _resolve_and_print(ctx: Ctx, info: dict) -> bool:
    for label, candidate in _resolve_candidates(info):
        g = ctx.graph(f"/shares/{share_id(candidate)}/driveItem")
        if g.status_code != 200:  # noqa: PLR2004
            OUT.out(
                f"  resolve ({label}): FAILED {g.status_code} {graph_error_code(g)}"
            )
            continue
        it = g.json()
        drive = (it.get("parentReference") or {}).get("driveId", "")
        OUT.out(
            f"  resolve ({label}): 200 name={it.get('name', '')} size={it.get('size')}"
        )
        OUT.out(f"    driveId={drive} itemId={it.get('id', '')}")
        OUT.out(f"    webUrl={it.get('webUrl', '')}")
        return True
    return False


def day_loop_links(msgs: list[dict]) -> list[str]:
    return list(
        dict.fromkeys(u for m in msgs for u in loop_hrefs(m.get("content", "")))
    )


def cmd_loop_links(ctx: Ctx, args) -> int:
    msgs = messages_on(ctx, args.conv, args.date, args.tz)
    links = day_loop_links(msgs)
    if not links:
        OUT.err(f"no Loop link on {args.date} ({len(msgs)} message(s) that day)")
        return EXIT_FAIL
    failures = 0
    for link in links:
        info = decode_loop_link(link)
        _print_decoded(info)
        failures += not _resolve_and_print(ctx, info)
    return EXIT_FAIL if failures else EXIT_OK


# ---------------------------------------------------------------------------- land


def recording_files(msgs: list[dict]) -> list[dict]:
    """Each distinct OneDrive recording file named by that day's recording messages."""
    found: dict[tuple[str, str], dict] = {}
    for attrs, content in _items(msgs, "onedriveForBusinessVideo"):
        key = (attrs.get("driveId", ""), attrs.get("driveItemId", ""))
        if all(key) and key not in found:
            name = re.search(r'<OriginalName v="([^"]*)"', content)
            found[key] = {
                "drive": key[0],
                "item": key[1],
                "name": name.group(1) if name else "",
            }
    return list(found.values())


def recording_start(name: str, stamp: str) -> datetime | None:
    """A recording's UTC start: from its OneDrive file name, else its message's arrival."""
    match = RECORDING_START.search(name or "")
    if match:
        try:
            return datetime.strptime(
                match.group(1) + match.group(2), "%Y%m%d%H%M%S"
            ).replace(tzinfo=UTC)
        except ValueError:
            pass
    return parse_utc(stamp)


def recording_finished(content: str) -> bool:
    """False for a recording progress message.

    Teams posts status Initial when a recording starts and ChunkFinished as it stops, each
    with no recording file; only the Success message names the finished recording.
    """
    status = RECORDING_STATUS.search(html.unescape(content or ""))
    return status is None or status.group(1) == "Success"


def instances_of(msgs: list[dict], tz: str) -> list[dict]:
    """One entry per finished recording, newest first, with its start as a local date and HH:MM.

    The recording message an entry came from rides along under "_msg" for land; it is
    dropped before anything is printed.
    """
    found: dict[tuple[str, str], dict] = {}
    for m in recordings(msgs):
        if not recording_finished(m.get("content", "")):
            continue
        files = recording_files([m])
        key = (
            (files[0]["drive"], files[0]["item"])
            if files
            else ("message", str(m.get("id", "")))
        )
        name = files[0]["name"] if files else ""
        start = recording_start(name, m.get("originalarrivaltime", ""))
        if key in found or start is None:
            continue
        local = start.astimezone(ZoneInfo(tz))
        found[key] = {
            "date": local.date().isoformat(),
            "time": local.strftime("%H:%M"),
            "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recording": name,
            "message_id": str(m.get("id", "")),
            "_msg": m,
        }
    return sorted(found.values(), key=lambda i: i["start_utc"], reverse=True)


def public(instance: dict) -> dict:
    return {k: v for k, v in instance.items() if not k.startswith("_")}


def instance_lines(instances: list[dict]) -> str:
    return "".join(
        f"\n  {i['date']} {i['time']}  {i['recording'] or i['message_id']}"
        for i in instances
    )


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def pick_by_time(day_instances: list[dict], wanted: str, day: str) -> dict:
    hits = [
        i
        for i in day_instances
        if abs(_minutes(i["time"]) - _minutes(wanted)) <= TIME_WINDOW_MIN
    ]
    if len(hits) != 1:
        found = "no recording" if not hits else f"{len(hits)} recordings"
        raise Refusal(
            f"{found} on {day} started within {TIME_WINDOW_MIN} minutes of {wanted}; "
            f"that day's recordings:{instance_lines(day_instances) or ' none'}"
        )
    return hits[0]


def instance_scope(
    instances: list[dict], chosen: dict, page: list[dict]
) -> tuple[list[dict], list[dict]]:
    """(the chosen recording's message, the message holding that meeting's Loop link).

    The Loop link is the newest one posted at or before the chosen recording message and
    after the previous instance's recording message, so a second meeting that day never
    picks up the first one's notes.
    """
    rec = chosen["_msg"]
    older = instances[instances.index(chosen) + 1 :]
    upper = parse_utc(rec.get("originalarrivaltime", ""))
    lower = (
        parse_utc(older[0]["_msg"].get("originalarrivaltime", "")) if older else None
    )
    linked = []
    for m in page:
        when = parse_utc(m.get("originalarrivaltime", ""))
        if (
            when
            and upper
            and when <= upper
            and (lower is None or when > lower)
            and loop_hrefs(m.get("content", ""))
        ):
            linked.append((when, m))
    newest = max(linked, key=lambda pair: pair[0])[1] if linked else None
    return [rec], [newest] if newest else []


def cmd_instances(ctx: Ctx, args) -> int:
    page = fetch_messages(ctx, args.conv, MAX_PAGE)
    found = instances_of(page, args.tz)
    if args.date:
        found = [i for i in found if i["date"] == args.date]
        warn_window(page, args.date, args.tz, found)
    rows = [public(i) for i in found]
    if args.json:
        OUT.out(json.dumps(rows, indent=2))
    else:
        for r in rows:
            OUT.out(
                f"{r['date']} {r['time']}\t{r['recording'] or '(no recording file)'}\t{r['message_id']}"
            )
    OUT.err(f"{len(rows)} instance(s), newest first")
    return EXIT_OK


def plain_name(name: str) -> str:
    return name if name and Path(name).name == name and not name.startswith(".") else ""


def git_would_track(path: Path) -> bool:
    """True when `path` lies in a git work tree that does not ignore it."""
    git = shutil.which("git")
    if not git:
        return False
    try:
        r = subprocess.run(  # noqa: S603 - fixed argv, git resolved via shutil.which
            [git, "-C", str(path.parent), "check-ignore", "-q", "--", path.name],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 1


def h1_at(page: str, title: str) -> int:
    m = re.search(
        r"<h1\b[^>]*>(?:\s|<[^>]+>)*" + re.escape(title) + r"(?:\s|<[^>]+>)*</h1>",
        page,
        re.I,
    )
    return m.start() if m else -1


def has_summary(page: str, summary: str, anchor: str) -> bool:
    at, follow = h1_at(page, summary), h1_at(page, anchor)
    return at >= 0 and (follow < 0 or at < follow)


def summary_fragment(text: str, heading: str) -> str:
    """The AI summary text as Loop-styled HTML under an H1. Bullets (-, * or a bullet sign)
    become list items nested by two-space indents; other non-empty lines become paragraphs."""
    out = [
        f'<h1 style="margin-top: 24px; margin-bottom: 8px;"><span>{html.escape(heading)}</span></h1>'
    ]
    depth = 0
    for raw in text.expandtabs(2).splitlines():
        stripped = raw.lstrip(" ")
        if not stripped.strip():
            continue
        bullet = re.match(r"(?:[-*]|\u2022)\s+(.*)", stripped)
        level = (len(raw) - len(stripped)) // 2 + 1 if bullet else 0
        out += [UL_OPEN] * max(level - depth, 0) + ["</ul>"] * max(depth - level, 0)
        depth = level
        body = html.escape((bullet.group(1) if bullet else stripped).rstrip())
        para = f'<div style="margin-bottom: 8px;"><span>{body}</span></div>'
        out.append(f"<li><span>{body}</span></li>" if bullet else para)
    out.extend("</ul>" for _ in range(depth))
    return "".join(out)


def splice_summary(page: str, fragment: str, anchor: str) -> str:
    follow = h1_at(page, anchor)
    if follow < 0:
        raise ValueError(
            f"the Loop page has no {anchor} heading to insert the summary before"
        )
    return page[:follow] + fragment + page[follow:]


def find_chromium(explicit: str) -> str:
    if explicit:
        return explicit
    hits = sorted(glob.glob(os.path.expanduser(CHROMIUM_GLOB)))
    return hits[-1] if hits else ""


def render_pdf(chromium: str, src: Path, dest: Path) -> str:
    """Print a local HTML file to PDF with headless Chromium and no network. Returns an error or ''."""
    if not chromium:
        return (
            f"no headless Chromium found (pass --chromium; looked for {CHROMIUM_GLOB})"
        )
    part = dest.with_name(dest.name + ".part")
    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            chromium,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--host-resolver-rules=MAP * ~NOTFOUND",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={part}",
            src.resolve().as_uri(),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=180, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"chromium did not run: {type(exc).__name__}"
    if not part.is_file() or not part.read_bytes().startswith(b"%PDF"):
        part.unlink(missing_ok=True)
        return "chromium wrote no PDF"
    os.replace(part, dest)
    return ""


@dataclass
class Landing:
    ctx: Ctx
    args: argparse.Namespace
    msgs: list[dict]
    loop_msgs: list[dict]
    data: Path
    derived: Path
    stem: str

    def say(self, text: str) -> None:
        OUT.out(text)


def land_vtt(job: Landing) -> bool:
    target = job.data / f"{job.stem}.vtt"
    if target.exists() and not job.args.force:
        job.say(f"vtt: kept existing {target}")
        return True
    if not has_recording(job.msgs):
        job.say(f"vtt: FAILED no {RECORDING_TYPE} object that day")
        return False
    body, source = fetch_transcript(job.ctx, job.msgs)
    if not body:
        job.say("vtt: FAILED no transcript route answered with WEBVTT")
        return False
    write_bytes(target, body, job.args.force)
    job.say(f"vtt: wrote {target} ({len(body)} bytes) from {source}")
    return True


def _recording_target(job: Landing, rec: dict) -> tuple[Path | None, int | None]:
    """Where the recording goes and its expected size; (None, None) after a reported failure."""
    meta = job.ctx.graph(
        f"/drives/{rec['drive']}/items/{rec['item']}?$select=name,size"
    )
    if meta.status_code != 200:  # noqa: PLR2004
        job.say(
            f"recording: FAILED {meta.status_code} {graph_error_code(meta)} (driveItem metadata)"
        )
        return None, None
    info = meta.json()
    name = plain_name(rec["name"]) or plain_name(info.get("name", ""))
    if not name:
        job.say(f"recording: FAILED driveItem {rec['item']} has no usable file name")
        return None, None
    size = info.get("size")
    return job.data / name, size if isinstance(size, int) else None


def _download_recording(
    job: Landing, rec: dict, target: Path, expected: int | None
) -> bool:
    part = target.with_name(target.name + ".part")
    url = f"{job.ctx.settings['graph_url']}/drives/{rec['drive']}/items/{rec['item']}/content"
    try:
        r = HTTP.download(url, job.ctx.bearer("graph"), part, 120)
    except OSError as exc:
        part.unlink(missing_ok=True)
        job.say(f"recording: FAILED download interrupted: {type(exc).__name__}")
        return False
    if not r.ok:
        part.unlink(missing_ok=True)
        job.say(
            f"recording: FAILED {r.status_code} {graph_error_code(r)} (GET driveItem content)"
        )
        return False
    size = part.stat().st_size
    if expected is not None and size != expected:
        part.unlink()
        job.say(f"recording: FAILED got {size} bytes, driveItem says {expected}")
        return False
    os.replace(part, target)
    job.say(f"recording: wrote {target} ({size} bytes) from driveItem {rec['item']}")
    return True


def land_recording(job: Landing, rec: dict) -> bool:
    name = plain_name(rec["name"])
    if name and (job.data / name).exists() and not job.args.force:
        job.say(f"recording: kept existing {job.data / name}")
        return True
    target, expected = _recording_target(job, rec)
    if target is None:
        return False
    if target.exists() and not job.args.force:
        job.say(f"recording: kept existing {target}")
        return True
    if git_would_track(target):
        job.say(
            f"recording: FAILED {target} is not ignored by git; a recording must stay untracked"
        )
        return False
    return _download_recording(job, rec, target, expected)


def resolve_loop_item(job: Landing) -> dict | None:
    """The first Loop link that day that Graph resolves to a .loop driveItem."""
    links = day_loop_links(job.loop_msgs)
    if not links:
        job.say("loop: FAILED no Loop link for that meeting")
        return None
    for link in links:
        info = decode_loop_link(link)
        for _, candidate in _resolve_candidates(info):
            g = job.ctx.graph(f"/shares/{share_id(candidate)}/driveItem")
            if g.status_code != 200:  # noqa: PLR2004
                job.say(
                    f"loop: {link[:80]} did not resolve: {g.status_code} {graph_error_code(g)}"
                )
                continue
            it = g.json()
            if str(it.get("name", "")).endswith(".loop"):
                drive = (it.get("parentReference") or {}).get("driveId", "")
                return {
                    "drive": drive,
                    "item": it.get("id", ""),
                    "name": it.get("name", ""),
                }
            job.say(f"loop: {link[:80]} is {it.get('name', '')!r}, not a .loop page")
            break
    job.say("loop: FAILED no Loop link resolved to a .loop page")
    return None


def loop_export(job: Landing, item: dict, fmt: str) -> bytes:
    url = f"{job.ctx.settings['graph_url']}/drives/{item['drive']}/items/{item['item']}/content?format={fmt}"
    r = HTTP.get(url, job.ctx.bearer("graph"), 120)
    if not r.ok:
        job.say(
            f"loop: FAILED {r.status_code} {graph_error_code(r)} (GET content?format={fmt})"
        )
        return b""
    return r.content


def _loop_page(job: Landing) -> tuple[bytes, dict | None]:
    page_path = job.data / f"{job.stem}.html"
    if page_path.exists() and not job.args.force:
        job.say(f"loop html: kept existing {page_path}")
        return page_path.read_bytes(), None
    item = resolve_loop_item(job)
    page = loop_export(job, item, "html") if item else b""
    if page:
        write_bytes(page_path, page, job.args.force)
        job.say(f"loop html: wrote {page_path} ({len(page)} bytes) from {item['name']}")
    return page, item


def _export_pdf(job: Landing, item: dict | None, present: bool) -> bool:
    pdf_path = job.data / f"{job.stem}.pdf"
    notes_only = job.data / f"{job.stem} (no {job.ctx.settings['summary_heading']}).pdf"
    target = pdf_path if present else notes_only
    if present:
        job.say(
            f"{job.ctx.settings['summary_heading']}: already in the Loop page; the Loop PDF export is the deliverable"
        )
    else:
        job.say(
            f"{job.ctx.settings['summary_heading']}: MISSING (the Loop page has no such heading "
            "and no --ai-summary file was given)"
        )
    if target.exists() and not job.args.force:
        job.say(f"notes pdf: kept existing {target}")
        return present
    item = item or resolve_loop_item(job)
    pdf = loop_export(job, item, "pdf") if item else b""
    if not pdf:
        return False
    write_bytes(target, pdf, job.args.force)
    job.say(
        f"notes pdf: wrote {target} ({len(pdf)} bytes) from Graph content?format=pdf"
    )
    return present


def _compose_pdf(job: Landing, page: str) -> bool:
    heading, anchor = (
        job.ctx.settings["summary_heading"],
        job.ctx.settings["anchor_heading"],
    )
    try:
        text = Path(job.args.ai_summary).read_text(encoding="utf-8")
        composed = splice_summary(page, summary_fragment(text, heading), anchor)
    except ValueError as exc:
        job.say(f"notes pdf: FAILED {exc}")
        return False
    composed_path = job.derived / f"{job.stem} composed.html"
    composed_path.parent.mkdir(parents=True, exist_ok=True)
    composed_path.write_text(composed, encoding="utf-8")
    pdf_path = job.data / f"{job.stem}.pdf"
    error = render_pdf(find_chromium(job.args.chromium), composed_path, pdf_path)
    if error:
        job.say(f"notes pdf: FAILED {error}")
        return False
    job.say(
        f"{heading}: composed from {job.args.ai_summary} before the {anchor} heading ({composed_path})"
    )
    job.say(
        f"notes pdf: wrote {pdf_path} ({pdf_path.stat().st_size} bytes), rendered locally"
    )
    return True


def land_loop(job: Landing) -> bool:
    pdf_path = job.data / f"{job.stem}.pdf"
    if pdf_path.exists() and not job.args.force:
        job.say(f"notes pdf: kept existing {pdf_path}")
        return True
    raw, item = _loop_page(job)
    if not raw:
        return False
    page = raw.decode("utf-8", "replace")
    present = has_summary(
        page, job.ctx.settings["summary_heading"], job.ctx.settings["anchor_heading"]
    )
    if present or not job.args.ai_summary:
        return _export_pdf(job, item, present)
    return _compose_pdf(job, page)


def step(label: str, fn, *fn_args) -> bool:
    """Run one artefact's fetch so a network error in it is reported without abandoning the others."""
    try:
        return fn(*fn_args)
    except (AuthError, Refusal, ConfigError):
        raise
    except Exception as exc:  # noqa: BLE001  (reported through the redacting writer)
        OUT.out(f"{label}: FAILED {type(exc).__name__}: {exc}")
        return False


def _scope(ctx: Ctx, args) -> tuple[list[dict], list[dict], str]:
    """(recording messages, Loop link messages, file name stem) for the instance to land.

    One recording that day, or none: the whole day, as one meeting. Several: only the one
    --time picks, named with its start so the day's instances never overwrite each other;
    without --time that is ambiguous and refused before anything is fetched or written.
    """
    page = fetch_messages(ctx, args.conv, MAX_PAGE)
    day_msgs = [
        m
        for m in page
        if on_local_date(m.get("originalarrivaltime", ""), args.date, args.tz)
    ]
    warn_window(page, args.date, args.tz, day_msgs)
    instances = instances_of(page, args.tz)
    same_day = [i for i in instances if i["date"] == args.date]
    if len(same_day) <= 1:
        if args.time:
            pick_by_time(same_day, args.time, args.date)
        return day_msgs, day_msgs, instance_stem(args.title, args.date, None)
    if not args.time:
        raise Refusal(
            f"{len(same_day)} recordings on {args.date}; pass --time HH:MM to land one:"
            f"{instance_lines(same_day)}"
        )
    chosen = pick_by_time(same_day, args.time, args.date)
    name = chosen["recording"] or chosen["message_id"]
    OUT.out(f"instance: {chosen['date']} {chosen['time']} {name}")
    msgs, loop_msgs = instance_scope(instances, chosen, page)
    return msgs, loop_msgs, instance_stem(args.title, args.date, chosen["time"])


def instance_stem(title: str, day: str, time: str | None) -> str:
    """The landed files' name stem: "<title> <date>", plus " HHMM" for one of several that day.

    STEM RULE, kept in step with meeting_retrieve.cli.instance_stem, which names the AI
    Summary inbox file "<stem> AI summary.txt" from it. Change both together.
    """
    stem = f"{title} {day}"
    return f"{stem} {time.replace(':', '')}" if time else stem


def cmd_land(ctx: Ctx, args) -> int:
    check_title(args.title)
    if args.ai_summary and not Path(args.ai_summary).is_file():
        raise Refusal(f"--ai-summary is not a file: {args.ai_summary!r}")
    root = Path(os.path.expanduser(args.out_root or str(ctx.settings["out_root"])))
    meeting_dir = root / (args.dir_name or args.date)
    data, derived = meeting_dir / "data", meeting_dir / "derived"
    msgs, loop_msgs, stem = _scope(ctx, args)
    job = Landing(ctx, args, msgs, loop_msgs, data, derived, stem)
    data.mkdir(parents=True, exist_ok=True)
    results = [step("vtt", land_vtt, job)]
    recs = recording_files(job.msgs)
    if not recs:
        job.say(
            "recording: FAILED no OneDrive recording file named in that day's recording messages"
        )
    results += [step("recording", land_recording, job, rec) for rec in recs] or [False]
    results.append(step("loop", land_loop, job))
    job.say(
        f"land {args.date}: {'complete' if all(results) else 'INCOMPLETE'} in {data}"
    )
    return EXIT_OK if all(results) else EXIT_FAIL


# ---------------------------------------------------------------------------- the one write


ANCHOR_RE = re.compile(
    r'<a\b[^>]*?href="(?P<href>[^"]*)"[^>]*>(?P<text>.*?)</a>', re.I | re.S
)
WEB_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _shown(markup: str) -> str:
    """The text a reader sees: tags removed, entities decoded, whitespace collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup or "")).split())


def link_findings(body: str) -> list[str]:
    """Why a body breaks the link rule: every web URL it references (an issue, a page, a repository or a
    file) is a hyperlink with readable display text, never a bare URL and never a link whose text is the URL."""
    findings = []
    for m in ANCHOR_RE.finditer(body or ""):
        href, text = html.unescape(m.group("href")), _shown(m.group("text"))
        if not text or text == href or WEB_URL_RE.fullmatch(text):
            findings.append(f"link {href} has no readable display text")
    for url in WEB_URL_RE.findall(_shown(ANCHOR_RE.sub(" ", body or ""))):
        findings.append(f"bare URL {url} is not a hyperlink with display text")
    return findings


def enforce_links(body: str, allow_bare: bool) -> None:
    findings = link_findings(body)
    if findings and not allow_bare:
        raise Refusal(
            "the body breaks the link rule (every web URL as a hyperlink with readable display text): "
            + "; ".join(findings)
            + ". Fix the body, or pass --allow-bare-urls if the account owner wants it as written."
        )
    for finding in findings:
        OUT.out(f"link rule waived by --allow-bare-urls: {finding}")


def cmd_send(settings: dict, args) -> int:
    """Preview by default; post only with --send and the approved body's sha256."""
    body_bytes = Path(args.file).read_bytes()
    digest = hashlib.sha256(body_bytes).hexdigest()
    dest = NOTES_CONV if args.to_notes else args.conv
    body = body_bytes.decode("utf-8")
    enforce_links(body, args.allow_bare_urls)
    if not args.send:
        OUT.out(
            f"destination: {dest}{' (Saved Messages)' if dest == NOTES_CONV else ''}"
        )
        OUT.out(f"sha256: {digest}")
        OUT.out("----- body (exact bytes that would be posted) -----")
        OUT.out(body)
        OUT.out("----- end body -----")
        OUT.out(
            "PREVIEW ONLY: nothing was posted. Send only after the account owner approves this text and destination:"
        )
        target = "--to-notes" if args.to_notes else f"--conv '{dest}'"
        OUT.out(f"  teams send {target} --file '{args.file}' --send --sha256 {digest}")
        return EXIT_OK
    if not args.sha256:
        raise Refusal(
            "--send needs --sha256 of the approved body (run without --send to get it)"
        )
    if args.sha256.lower() != digest:
        raise Refusal(
            f"body sha256 is {digest}, not the approved {args.sha256}; the file changed after approval"
        )
    ctx = Ctx(settings, load_tokens(settings))
    url = f"{ctx.chat_base()}/{quote(dest, safe='')}/messages"
    payload = {"content": body, "messagetype": "RichText/Html", "contenttype": "html"}
    r = HTTP.post_json(url, ctx.chat_headers(), payload, 15)
    if r.status_code in HTTP_AUTH_FAIL:
        raise AuthError(f"the chat service refused the send with {r.status_code}")
    if not r.ok:
        OUT.err(f"send failed: chat service {r.status_code}")
        return EXIT_FAIL
    OUT.out(f"sent to {dest} ({len(body_bytes)} bytes, sha256 {digest})")
    return EXIT_OK


# ---------------------------------------------------------------------------- recap AI summary


class RecapStop(RuntimeError):
    """The read cannot go on from what the tab shows; the message says what to do."""


def recap_port(settings: Mapping, flag: int | None) -> int:
    """--port, else the teams config's cdp_port."""
    if flag:
        return flag
    if int(settings.get("cdp_port") or 0):
        return int(settings["cdp_port"])
    raise ConfigError(
        "no CDP port: set cdp_port in the settings TOML or pass --port, the port of a "
        "Chromium-family browser started with --remote-debugging-port"
    )


def recap_label(day: str) -> str:
    """The long date a Teams meeting card shows, for example "October 19, 2026"."""
    d = date.fromisoformat(day)
    return f"{d:%B} {d.day}, {d.year}"


def host_matches(url: str, hosts) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in hosts)


def _refuse_unlisted(method: str) -> None:
    if method not in RECAP_METHODS:
        raise Refusal(f"recap-summary never sends {method}")


class RecapPage:
    """One attached session on the Teams tab, evaluating the steps in recap_summary.js."""

    def __init__(self, cdp, session_id: str) -> None:
        self.cdp = cdp
        self.sid = session_id
        self.lib = RECAP_JS.read_text(encoding="utf-8")
        self.collab: list[tuple[str, object, object]] = []
        self.finished: set[str] = set()

    def ev(self, step: str, arg=None):
        expression = f"({self.lib})({json.dumps(step)}, {json.dumps(arg)})"
        reply = self.cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session_id=self.sid,
            timeout=RECAP_EVAL_TIMEOUT_S,
        )
        self._collect()
        if "exceptionDetails" in reply:
            text = reply["exceptionDetails"].get("text", "")
            raise RecapStop(f"the page script failed at step {step}: {text}")
        return reply.get("result", {}).get("value")

    def _collect(self) -> None:
        for event in self.cdp.events:
            params = event.get("params", {})
            if event.get("method") == "Network.loadingFinished":
                self.finished.add(params.get("requestId"))
            if event.get("method") != "Network.responseReceived":
                continue
            resp = params.get("response", {})
            if COLLAB_PATH_MARK in resp.get("url", ""):
                self.collab.append(
                    (params.get("requestId"), resp.get("status"), resp.get("mimeType"))
                )
        self.cdp.events.clear()

    def wait(
        self, probe: Callable[[], object], what: str, polls: int = RECAP_WAIT_POLLS
    ):
        for _ in range(polls):
            value = probe()
            if value:
                return value
            time.sleep(RECAP_POLL_S)
        raise RecapStop(f"{what} never appeared")

    def loaded(self, chat: str) -> bool:
        """Whether a freshly opened Teams tab shows a chat yet; False while it is still loading."""
        try:
            return bool(self.ev("ready", chat))
        except Exception:  # noqa: BLE001  (a navigating page has no context to evaluate in)
            return False


def _find_card(page: RecapPage, label: str) -> None:
    """Scroll the chat's message list up until exactly one recap card for the date renders."""
    last, still = None, 0
    for _ in range(RECAP_SCROLL_STEPS):
        found = page.ev("find_card", label)
        if found == 1:
            return
        if found:
            raise RecapStop(
                f"{found} 'View recap' cards for {label} in this chat; the reader opens one only"
            )
        position = page.ev("scroll_up")
        if position is None:
            raise RecapStop(
                "the chat shows no message list to find the meeting card in"
            )
        still = still + 1 if position == last else 0
        last = position
        if still >= RECAP_STILL_STEPS:
            break
        time.sleep(RECAP_POLL_S)
    raise RecapStop(
        f"no 'View recap' card for {label} rendered after scrolling to the top of the loaded "
        "chat: check the chat name and the meeting's date"
    )


def _expand_notes(page: RecapPage) -> None:
    for _ in range(RECAP_EXPAND_PASSES):
        result = page.ev("expand")
        if not result or not result["toggled"]:
            return
        page.wait(lambda: page.ev("expand_pending") == 0, "the expanded notes rows")


def _collab_report(page: RecapPage, needle: str) -> None:
    for _ in range(RECAP_WAIT_POLLS):
        if page.collab and all(rid in page.finished for rid, _, _ in page.collab):
            break
        time.sleep(RECAP_POLL_S)
        page.ev("sections")
    if not page.collab:
        OUT.err("readcollabobject: no response seen while Recap opened")
    for request_id, status, mime in page.collab:
        try:
            body = page.cdp.call(
                "Network.getResponseBody",
                {"requestId": request_id},
                session_id=page.sid,
            )
        except Exception as exc:  # noqa: BLE001  (cdp_client.CdpError; its text carries no params)
            OUT.err(f"readcollabobject: status {status}, body unreadable: {exc}")
            continue
        raw = body.get("body", "")
        if body.get("base64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
            keys = sorted(parsed) if isinstance(parsed, dict) else type(parsed).__name__
        except ValueError:
            keys = "not JSON"
        found = "yes" if needle in raw else "no"
        OUT.err(
            f"readcollabobject: status {status}, {mime}, {len(raw)} bytes, "
            f"top-level keys {keys}, contains the needle: {found}"
        )


def _select_chat(page: RecapPage, chat: str) -> dict:
    """Open the named chat from Teams' left navigation unless the tab already shows it."""
    state = page.ev("state")
    if state["chat"] == chat:
        return state
    if page.ev("select_chat", chat) != "clicked":
        shown = (
            f"shows the chat {state['chat']!r}" if state["chat"] else "shows no chat"
        )
        raise Refusal(
            f"the Teams tab {shown} and its chat list has no {chat!r} row to open: open that "
            "chat yourself, then rerun"
        )
    page.wait(lambda: page.ev("state")["chat"] == chat, f"the chat {chat!r}")
    OUT.err(
        f"recap-summary: opened the chat {chat!r} in the Teams tab and left it there"
    )
    return page.ev("state")


def read_recap(page: RecapPage, args) -> dict:
    page.ev("guard")
    state = _select_chat(page, args.chat)
    if state["recap_open"]:
        raise Refusal(
            "Recap is already open in the Teams tab and the reader cannot tell which meeting it "
            "shows: return the tab to the chat yourself, then rerun"
        )
    label = recap_label(args.date)
    _find_card(page, label)
    if args.collab_needle:
        page.cdp.call("Network.enable", {}, session_id=page.sid)
    if page.ev("open_recap", label) != "clicked":
        raise RecapStop(
            f"the 'View recap' card for {label} went away before it was clicked"
        )
    page.wait(lambda: page.ev("state")["recap_open"], "the Recap's 'AI summary' tab")
    page.ev("select_tab")
    page.wait(
        lambda: all(page.ev("sections").values()),
        "an AI summary with 'Meeting notes' and 'Follow-up tasks' sections",
    )
    _expand_notes(page)
    result = page.ev("extract")
    if args.collab_needle:
        _collab_report(page, args.collab_needle)
        page.cdp.call("Network.disable", {}, session_id=page.sid)
    if not result or not result["heads"]:
        raise RecapStop("the 'Meeting notes' section has no rows")
    if result["collapsed"]:
        raise RecapStop(f"{result['collapsed']} notes rows stayed collapsed")
    return result


def _park(cdp, sid: str, url: str) -> None:
    """Leave a tab the reader opened on a blank page; it is never closed, so it is never the
    window's last tab gone."""
    try:
        cdp.call("Page.navigate", {"url": url}, session_id=sid)
        OUT.err(f"recap-summary: left the tab it opened on {url}")
    except Exception as exc:  # noqa: BLE001  (cdp_client.CdpError; the read's own result stands)
        OUT.err(f"recap-summary: could not move the tab it opened to {url}: {exc}")


def _read_in_tab(cdp, tabs: list[dict], settings: Mapping, args) -> dict:
    """Bring the first Teams tab to the front, or open one when there is none, then read."""
    if tabs:
        target = tabs[0]["id"]
        cdp.call("Target.activateTarget", {"targetId": target})
    else:
        url = settings["recap_open_url"]
        target = cdp.call("Target.createTarget", {"url": url})["targetId"]
        OUT.err(
            f"recap-summary: no Teams tab was open, so it opened a Teams tab at {url}"
        )
    sid = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})[
        "sessionId"
    ]
    page = RecapPage(cdp, sid)
    try:
        if not tabs:
            page.wait(
                lambda: page.loaded(args.chat), "a loaded Teams page", RECAP_OPEN_POLLS
            )
        page.wait(
            lambda: page.ev("visible"),
            "the Teams tab in front (is the CDP Chrome window minimized?)",
        )
        return read_recap(page, args)
    finally:
        if not tabs:
            _park(cdp, sid, settings["recap_park_url"])
        cdp.call("Target.detachFromTarget", {"sessionId": sid})


def cmd_recap_summary(settings: Mapping, args) -> int:
    """Read the Recap AI summary from Teams web in the CDP browser, opening the chat."""
    out = Path(args.out).expanduser() if args.out else None
    if out and out.exists() and not args.force:
        raise Refusal(f"{out} exists; pass --force to replace it")
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import cdp_client as sso_cdp  # noqa: PLC0415  (the bundled CDP client, needed by this command only)

    port = recap_port(settings, args.port)
    try:
        targets = sso_cdp.list_targets_http(port)
    except sso_cdp.CdpError as exc:
        OUT.err(f"recap-summary: {exc}")
        return EXIT_FAIL
    pages = [t for t in targets if t.get("type") == "page"]
    hosts = settings["recap_tab_hosts"]
    tabs = [t for t in pages if host_matches(t.get("url", ""), hosts)]
    if len(tabs) > 1:
        OUT.err(f"recap-summary: {len(tabs)} Teams tabs are open; reading the first")
    cdp = sso_cdp.Cdp(sso_cdp.browser_ws_url(port), on_call=_refuse_unlisted)
    opened = not tabs
    try:
        result = _read_in_tab(cdp, tabs, settings, args)
    except RecapStop as exc:
        OUT.err(f"recap-summary: {exc}")
        return EXIT_FAIL
    finally:
        cdp.close()
        OUT.err("cdp methods: " + ", ".join(sorted(set(cdp.method_log))))
    after = sum(1 for t in sso_cdp.list_targets_http(port) if t.get("type") == "page")
    if after != len(pages) + opened:
        OUT.err(f"recap-summary: the page count moved from {len(pages)} to {after}")
        return EXIT_FAIL
    badges = result["badges"]
    OUT.err(
        f"recap-summary: {result['heads']} notes, {result['subs']} sub-bullets, "
        f"{result['items']} follow-ups, {badges} count badge{'' if badges == 1 else 's'} dropped"
    )
    if out:
        write_bytes(out, result["text"].encode("utf-8"), args.force)
        OUT.err(f"wrote {out}")
    else:
        OUT.out(result["text"].rstrip("\n"))
    return EXIT_OK


# ---------------------------------------------------------------------------- CLI


def _dated(sub, name: str, help_text: str) -> argparse.ArgumentParser:
    s = sub.add_parser(name, help=help_text)
    s.add_argument("--conv", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--tz", help="IANA zone for local dates (config: timezone)")
    return s


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 - one statement per option, nothing else
    p = argparse.ArgumentParser(
        prog="teams", description="Read Microsoft Teams; send only when approved."
    )
    p.add_argument(
        "--config",
        help=f"settings TOML (env {ENV_CONFIG}; default {DEFAULT_CONFIG_PATH})",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser(
        "chats", help="list chats whose topic matches a query, with their ids"
    )
    s.add_argument("--query", default="")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument(
        "--json", action="store_true", help='a JSON list of {"conv", "topic"}'
    )

    s = sub.add_parser(
        "instances",
        help="one entry per recording, newest first: local start date and time, file name",
    )
    s.add_argument("--conv", required=True)
    s.add_argument("--date", help="only recordings that started on this local date")
    s.add_argument(
        "--tz", help="IANA zone for local dates and times (config: timezone)"
    )
    s.add_argument(
        "--json",
        action="store_true",
        help='a JSON list of {"date", "time", "start_utc", "recording", "message_id"}',
    )

    s = sub.add_parser("messages", help="one conversation as text, newest first")
    s.add_argument("--conv", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--tz")

    s = sub.add_parser(
        "thread", help="id, local time, sender, type and excerpt per message"
    )
    s.add_argument("--conv", required=True)
    s.add_argument("--limit", type=int, default=MAX_PAGE)
    s.add_argument("--date")
    s.add_argument("--tz")
    s.add_argument("--json", action="store_true")

    s = _dated(sub, "vtt", "save that date's transcript as the raw WEBVTT file")
    s.add_argument("--out", required=True)
    s.add_argument("--title", help="file name stem (config: title)")
    s.add_argument("--force", action="store_true")

    _dated(sub, "loop-links", "decode and resolve that date's Loop links")
    _dated(sub, "recording-uris", "recording uri= values and gateway ids for that date")

    s = _dated(
        sub,
        "land",
        "land one meeting instance's .vtt, recording, Loop HTML and notes PDF",
    )
    s.add_argument(
        "--time",
        help="local start HH:MM; picks one of several recordings that day (within 45 minutes)",
    )
    s.add_argument(
        "--out-root",
        help="write into OUT_ROOT/<date>/data and OUT_ROOT/<date>/derived (config: out_root)",
    )
    s.add_argument(
        "--dir-name",
        help="the meeting directory under OUT_ROOT in place of <date>; an ad hoc meeting uses <slug>-<date>",
    )
    s.add_argument("--title", help="file name stem (config: title)")
    s.add_argument(
        "--ai-summary",
        help="text of the Recap AI summary tab, when the Loop page lacks it",
    )
    s.add_argument(
        "--chromium",
        help="headless Chromium for the local PDF render (config: chromium)",
    )
    s.add_argument("--force", action="store_true")

    s = sub.add_parser(
        "recap-summary",
        help="read a meeting's Recap AI summary from the Teams tab open in the CDP browser",
    )
    s.add_argument(
        "--chat", required=True, help="the chat title the Teams tab must show"
    )
    s.add_argument("--date", required=True, help="the meeting date, YYYY-MM-DD")
    s.add_argument("--out", help="write the text here instead of stdout")
    s.add_argument("--force", action="store_true")
    s.add_argument("--port", type=int, help="CDP port (config: cdp_port)")
    s.add_argument(
        "--collab-needle",
        help="also report the readcollabobject response's top-level keys and whether it "
        "contains this text; never its body",
    )

    s = sub.add_parser(
        "send", help="preview a message; post it only with --send --sha256"
    )
    dest = s.add_mutually_exclusive_group(required=True)
    dest.add_argument("--conv")
    dest.add_argument(
        "--to-notes",
        action="store_true",
        help="Saved Messages (48:notes), the rehearsal target",
    )
    s.add_argument("--file", required=True)
    s.add_argument("--send", action="store_true")
    s.add_argument("--sha256")
    s.add_argument(
        "--allow-bare-urls",
        action="store_true",
        help="post a body carrying a bare URL or a link without display text, when the owner wants it so",
    )
    return p


def valid_date(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").date().isoformat()  # noqa: DTZ007
    except ValueError:
        raise Refusal(f"--date must be YYYY-MM-DD: {day!r}") from None


def valid_tz(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise Refusal(f"not a known IANA zone: {tz!r}") from None
    return tz


def apply_settings(args, settings: dict) -> None:
    """Fill each unset flag from settings, then validate the date and zone."""
    for flag, key in (("tz", "timezone"), ("title", "title"), ("chromium", "chromium")):
        if hasattr(args, flag) and not getattr(args, flag):
            setattr(args, flag, str(settings[key]))
    if getattr(args, "date", None):
        args.date = valid_date(args.date)
    if getattr(args, "tz", None):
        valid_tz(args.tz)
    if getattr(args, "time", None) and not TIME_RE.match(args.time):
        raise Refusal(f"--time must be HH:MM (24-hour): {args.time!r}")


READS = {
    "chats": cmd_chats,
    "instances": cmd_instances,
    "messages": cmd_messages,
    "thread": cmd_thread,
    "vtt": cmd_vtt,
    "loop-links": cmd_loop_links,
    "recording-uris": cmd_recording_uris,
    "land": cmd_land,
}


def run(args, settings: dict) -> int:
    apply_settings(args, settings)
    if args.cmd == "send":
        return cmd_send(settings, args)
    if args.cmd == "recap-summary":
        return cmd_recap_summary(settings, args)
    return READS[args.cmd](Ctx(settings, load_tokens(settings)), args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    hint = DEFAULTS["login_hint"]
    try:
        settings = load_settings(args.config)
        hint = str(settings["login_hint"])
        return run(args, settings)
    except Refusal as exc:
        OUT.err(f"refused: {exc}")
        return EXIT_REFUSED
    except ConfigError as exc:
        OUT.err(f"config: {exc}")
        return EXIT_CONFIG
    except AuthError as exc:
        OUT.err(f"not authenticated: {exc}")
        OUT.err(hint)
        return EXIT_AUTH
    except Exception as exc:  # noqa: BLE001  (last resort: never let a traceback carry a token)
        OUT.err(f"error: {type(exc).__name__}: {exc}")
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
