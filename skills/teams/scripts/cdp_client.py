"""A minimal Chrome DevTools Protocol client on the standard library, for `recap-summary`.

Every method that leaves the process passes through `Cdp.call`, which records the method name
and nothing else (never the params), and hands it to an optional `on_call` hook first. That is
what lets teams.py refuse any CDP method outside its own fixed list before it is sent.

No Origin header is sent on the WebSocket upgrade. Chromium 111+ rejects an upgrade whose Origin
is not on the --remote-allow-origins list and accepts one that carries none, so the browser need
not open its origin allowlist: a web page's script, which always carries an Origin, is refused,
while this client is admitted.

Only what recap-summary needs: an HTTP GET of /json/version and /json/list on the loopback, one
WebSocket to the browser endpoint, unfragmented text frames (Chromium does not fragment), and a
ping answered with a pong. Page-close methods are refused outright: this client never closes a
tab, so it can never close a window's last one. Python 3.11+.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

DEFAULT_TIMEOUT_S = 10.0
_MAX_HANDSHAKE_BYTES = 65536
_LEN_ONE_BYTE_MAX = 126  # payload lengths below this fit the 7-bit field
_LEN_TWO_BYTES = 126  # marker: a 16-bit length follows
_LEN_EIGHT_BYTES = 127  # marker: a 64-bit length follows
_LEN_TWO_BYTES_MAX = 65536
_OP_CONTINUATION = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA
_CDP_METHOD_TIMEOUT_S = 30.0
_MAX_KEPT_EVENTS = 200

#: The CDP methods that close a page target, refused before they are sent.
PAGE_CLOSE_METHODS = frozenset({"Target.closeTarget", "Page.close"})


class CdpError(Exception):
    """A transport failure or a CDP error reply. Its text never carries call params."""


def http_json(port: int, path: str, timeout: float = 3.0):
    """GET http://127.0.0.1:<port><path> and decode the JSON body."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 -- loopback only
            return json.load(response)
    except (OSError, ValueError) as exc:
        raise CdpError(f"no CDP endpoint answered {url}: {exc.__class__.__name__}") from exc


def browser_ws_url(port: int, timeout: float = 3.0) -> str:
    """The browser target's WebSocket URL from /json/version."""
    version = http_json(port, "/json/version", timeout)
    ws_url = version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None
    if not isinstance(ws_url, str) or not ws_url.startswith("ws://"):
        raise CdpError(f"/json/version on port {port} carried no webSocketDebuggerUrl")
    return ws_url


def list_targets_http(port: int, timeout: float = 3.0) -> list[dict]:
    """Every open target's descriptor from /json/list: HTTP only, no WebSocket, no side effect."""
    listing = http_json(port, "/json/list", timeout)
    return [entry for entry in listing if isinstance(entry, dict)] if isinstance(listing, list) else []


class WebSocket:
    """An RFC 6455 client good for one DevTools connection."""

    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "ws" or not parts.hostname or not parts.port:
            raise CdpError("the CDP WebSocket URL must be ws://host:port/path")
        self._sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._handshake(parts)

    def _handshake(self, parts) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parts.hostname}:{parts.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CdpError("the CDP endpoint closed the connection during the handshake")
            response += chunk
            if len(response) > _MAX_HANDSHAKE_BYTES:
                raise CdpError("the CDP endpoint sent an oversized handshake response")
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in status_line:
            raise CdpError(f"the CDP endpoint refused the WebSocket upgrade: {status_line!r}")

    def settimeout(self, timeout: float | None) -> None:
        self._sock.settimeout(timeout)

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x80 | _OP_TEXT])
        length = len(payload)
        if length < _LEN_ONE_BYTE_MAX:
            header.append(0x80 | length)
        elif length < _LEN_TWO_BYTES_MAX:
            header.append(0x80 | _LEN_TWO_BYTES)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | _LEN_EIGHT_BYTES)
            header += struct.pack("!Q", length)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + mask + masked)

    def _recv_exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self._sock.recv(count - len(data))
            if not chunk:
                raise CdpError("the CDP endpoint closed the connection")
            data += chunk
        return data

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._recv_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == _LEN_TWO_BYTES:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == _LEN_EIGHT_BYTES:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def recv_text(self) -> str:
        """The next text message, answering pings and reassembling continuations on the way."""
        buffer = b""
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == _OP_PING:
                self._send_control(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                raise CdpError("the CDP endpoint closed the WebSocket")
            if opcode in (_OP_TEXT, _OP_BINARY, _OP_CONTINUATION):
                buffer += payload
                if fin:
                    return buffer.decode("utf-8")

    def _send_control(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)])
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def close(self) -> None:
        try:
            self._send_control(_OP_CLOSE, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class Cdp:
    """JSON-RPC over one browser-level WebSocket, with flattened sessions.

    `method_log` holds every method name sent, in order; `on_call` (if given) sees the same names
    before they go out and may raise to refuse one. Neither ever sees params or results.
    """

    def __init__(
        self,
        ws_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        on_call: Callable[[str], None] | None = None,
    ) -> None:
        self._ws = WebSocket(ws_url, timeout=timeout)
        self._next_id = 0
        self.method_log: list[str] = []
        self.events: list[dict] = []
        self._on_call = on_call

    def call(
        self,
        method: str,
        params: dict | None = None,
        *,
        session_id: str | None = None,
        timeout: float = _CDP_METHOD_TIMEOUT_S,
    ) -> dict:
        if method in PAGE_CLOSE_METHODS:
            raise CdpError(f"refused {method}: this client never closes a tab")
        self._next_id += 1
        message_id = self._next_id
        message: dict = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self.method_log.append(method)
        if self._on_call:
            self._on_call(method)
        self._ws.settimeout(timeout)
        try:
            self._ws.send_text(json.dumps(message))
        except OSError as exc:
            raise CdpError(f"sending {method} failed: {exc.__class__.__name__}") from exc
        reply = self._await_reply(method, message_id, timeout)
        error = reply.get("error")
        if error is not None:
            text = error.get("message", "") if isinstance(error, dict) else ""
            raise CdpError(f"{method} failed: {text or 'unknown CDP error'}")
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def _next_message(self, method: str, deadline: float) -> dict | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CdpError(f"{method} did not answer within the timeout")
        self._ws.settimeout(remaining)
        try:
            raw = self._ws.recv_text()
        except TimeoutError as exc:
            raise CdpError(f"{method} did not answer within the timeout") from exc
        except OSError as exc:
            raise CdpError(f"receiving the reply to {method} failed: {exc.__class__.__name__}") from exc
        try:
            reply = json.loads(raw)
        except ValueError:
            return None
        return reply if isinstance(reply, dict) else None

    def _await_reply(self, method: str, message_id: int, timeout: float) -> dict:
        """Read until the reply to `message_id` arrives; events on the way are kept, bounded."""
        deadline = time.monotonic() + timeout
        while True:
            reply = self._next_message(method, deadline)
            if reply is None:
                continue
            if reply.get("id") == message_id:
                return reply
            if "method" in reply:
                self.events.append(reply)
                del self.events[:-_MAX_KEPT_EVENTS]

    def close(self) -> None:
        self._ws.close()
