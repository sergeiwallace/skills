#!/usr/bin/env python3
"""Refuse a recording whose container claims more duration than its payload contains.

Run this FIRST, before extracting audio or a frame grid. Every later step in this
directory sizes its coverage from the recording's duration, and a container duration is
not evidence that the frames are there.

The failure this exists to catch is a container whose declared duration exceeds its
decodable payload. A recording can look intact to a vendor tool while its last packets
end well before the declared duration. This is an upstream artifact to detect, never a
bug to repair here.

ffprobe's duration lies in the reassuring direction: it reports a clean 27:47 with no hint
of damage. Anything that sizes frame coverage from that number silently under-covers the
recording and reports success, which is exactly what happened -- the shortfall surfaced
hours later when an analyst noticed the frames ran out.

The check compares the container's declared duration against the last decodable packet of
each stream, and reports how much of the recording is actually usable so a caller can
bound its frame coverage honestly instead of assuming full length.

Cost: a healthy file is cleared in well under a second, because the first pass seeks into
the last `--tail-window` seconds instead of reading the file. A full sequential scan runs
only for a stream that pass fails, and only to quantify how much is missing.

Usage:
    python recording_integrity.py RECORDING [--tolerance 2.0] [--json] [--warn-only]

Exit status:
    0  the payload backs the declared duration (or --warn-only was passed)
    1  truncated: the container claims payload it does not contain
    2  the file could not be probed at all

"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

# Chosen from measurement, not intuition. A declared duration includes the final
# packet's duration while a presentation timestamp does not, so intact streams have a
# small gap. Two seconds stays well above an ordinary final packet interval while
# detecting a shortfall large enough to change a frame-coverage decision.
DEFAULT_TOLERANCE_SECONDS = 2.0

# How far back from the declared end the fast first pass seeks. Wide enough that a sparse
# keyframe index still yields packets on an intact file.
DEFAULT_TAIL_WINDOW_SECONDS = 30.0

STATUS_OK = "ok"
STATUS_TRUNCATED = "truncated"
STATUS_UNREADABLE = "unreadable"

EXIT_OK = 0
EXIT_TRUNCATED = 1
EXIT_UNREADABLE = 2

# Decoder complaints worth surfacing verbatim as corroborating evidence.
DIAGNOSTIC_MARKERS = (
    "partial file",
    "Invalid NAL unit size",
    "Error splitting the input",
    "missing picture in access unit",
    "Invalid data found",
    "moov atom not found",
)


class ProbeError(RuntimeError):
    """ffprobe could not describe the file at all."""


@dataclass(frozen=True)
class StreamObservation:
    """What one stream declares about itself, next to what its packets actually show."""

    index: int
    codec_type: str
    declared_duration: float
    declared_packets: int | None = None
    last_packet_pts: float | None = None
    observed_packets: int | None = None
    scanned_fully: bool = False

    @property
    def usable_duration(self) -> float:
        """Seconds of this stream a consumer can actually read."""
        return 0.0 if self.last_packet_pts is None else self.last_packet_pts

    @property
    def gap(self) -> float:
        """Declared duration minus the last packet timestamp. Never negative."""
        return max(0.0, self.declared_duration - self.usable_duration)


@dataclass(frozen=True)
class IntegrityVerdict:
    """The answer a caller acts on: is it whole, and if not, how much can be used."""

    status: str
    declared_duration: float
    usable_duration: float
    streams: tuple[StreamObservation, ...]
    tolerance: float
    reasons: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    path: str = ""
    size_bytes: int | None = None

    @property
    def missing_duration(self) -> float:
        return max(0.0, self.declared_duration - self.usable_duration)

    @property
    def usable_fraction(self) -> float:
        if self.declared_duration <= 0:
            return 0.0
        return self.usable_duration / self.declared_duration

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def evaluate(
    container_duration: float,
    observations: list[StreamObservation],
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
) -> IntegrityVerdict:
    """Decide whether the payload backs the declared duration, and bound what is usable.

    The declared reference is the container duration, because that is the number a caller
    reads and sizes its work from. The usable duration is the EARLIEST stream end, not the
    latest: past that point some stream has nothing, so no consumer can honestly claim
    coverage there.
    """
    if not observations:
        return IntegrityVerdict(
            status=STATUS_UNREADABLE,
            declared_duration=max(0.0, container_duration),
            usable_duration=0.0,
            streams=(),
            tolerance=tolerance,
            reasons=("no audio or video stream could be read",),
        )

    declared = max([container_duration, *(o.declared_duration for o in observations)])
    usable = min(o.usable_duration for o in observations)
    missing = max(0.0, declared - usable)

    reasons: list[str] = []
    for observation in observations:
        label = f"{observation.codec_type[:1]}:{observation.index}"
        if observation.last_packet_pts is None:
            reasons.append(f"{label} {observation.codec_type}: no decodable packet found at all")
        elif observation.gap > tolerance:
            reasons.append(
                f"{label} {observation.codec_type}: declares {observation.declared_duration:.3f}s "
                f"but its last packet is at {observation.last_packet_pts:.3f}s "
                f"({observation.gap:.3f}s beyond tolerance {tolerance:.3f}s)"
            )
        if (
            observation.scanned_fully
            and observation.declared_packets is not None
            and observation.observed_packets is not None
            and observation.observed_packets < observation.declared_packets
        ):
            reasons.append(
                f"{label} {observation.codec_type}: declares {observation.declared_packets} packets, "
                f"{observation.observed_packets} are present "
                f"({observation.declared_packets - observation.observed_packets} absent)"
            )

    status = STATUS_TRUNCATED if missing > tolerance else STATUS_OK
    return IntegrityVerdict(
        status=status,
        declared_duration=declared,
        usable_duration=usable,
        streams=tuple(observations),
        tolerance=tolerance,
        reasons=tuple(reasons),
    )


def _ffprobe_path() -> str:
    resolved = shutil.which("ffprobe")
    if resolved is None:
        raise ProbeError("ffprobe not found on PATH; run this skill's scripts/install_deps.sh")
    return resolved


def _run_ffprobe(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, ffprobe resolved via shutil.which
        [_ffprobe_path(), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _as_float(value: object) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return None if math.isnan(parsed) else parsed  # reject NaN


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def probe_metadata(path: Path) -> tuple[float, int | None, list[StreamObservation]]:
    """Read the container's own claims: duration, size, and per-stream duration/frame count."""
    result = _run_ffprobe(
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if result.returncode != 0:
        raise ProbeError(f"ffprobe could not read {path}: {result.stderr.strip() or 'no diagnostic'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned unparseable output for {path}: {exc}") from exc

    container = payload.get("format", {})
    container_duration = _as_float(container.get("duration")) or 0.0
    size_bytes = _as_int(container.get("size"))

    observations: list[StreamObservation] = []
    for stream in payload.get("streams", []):
        codec_type = str(stream.get("codec_type", ""))
        if codec_type not in ("video", "audio"):
            continue
        declared = _as_float(stream.get("duration"))
        observations.append(
            StreamObservation(
                index=_as_int(stream.get("index")) or 0,
                codec_type=codec_type,
                declared_duration=declared if declared is not None else container_duration,
                declared_packets=_as_int(stream.get("nb_frames")),
            )
        )
    return container_duration, size_bytes, observations


def _packet_timestamps(path: Path, selector: str, start: float | None) -> tuple[float | None, int, str]:
    """Return (last pts_time, packet count, stderr) for one stream, optionally from `start`."""
    args = ["-v", "error", "-select_streams", selector]
    if start is not None:
        args += ["-read_intervals", f"{max(0.0, start):.3f}%"]
    args += ["-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)]
    result = _run_ffprobe(args)

    last: float | None = None
    count = 0
    for line in result.stdout.splitlines():
        value = _as_float(line.strip().rstrip(","))
        if value is None:
            continue
        count += 1
        if last is None or value > last:
            last = value
    return last, count, result.stderr


def _collect_diagnostics(stderr_blobs: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for blob in stderr_blobs:
        for line in blob.splitlines():
            text = line.strip()
            if not any(marker in text for marker in DIAGNOSTIC_MARKERS):
                continue
            # Drop the ffmpeg context prefix ("[h264 @ 0x...] ") so addresses do not
            # make two identical complaints look like two different ones.
            message = text.split("] ", 1)[1] if text.startswith("[") and "] " in text else text
            if message not in seen:
                seen.append(message)
    return tuple(seen)


def inspect(
    path: Path,
    tolerance: float = DEFAULT_TOLERANCE_SECONDS,
    tail_window: float = DEFAULT_TAIL_WINDOW_SECONDS,
) -> IntegrityVerdict:
    """Probe `path` and return the verdict, escalating to a full scan only when needed."""
    container_duration, size_bytes, declared_streams = probe_metadata(path)
    if not declared_streams:
        verdict = evaluate(container_duration, [], tolerance)
        return replace(verdict, path=str(path), size_bytes=size_bytes)

    stderr_blobs: list[str] = []
    observations: list[StreamObservation] = []
    for declared in declared_streams:
        selector = f"{declared.codec_type[:1]}:0"
        tail_start = declared.declared_duration - tail_window
        last, _count, stderr = _packet_timestamps(path, selector, tail_start if tail_start > 0 else None)
        stderr_blobs.append(stderr)

        cleared = last is not None and (declared.declared_duration - last) <= tolerance
        if cleared:
            observations.append(
                StreamObservation(
                    index=declared.index,
                    codec_type=declared.codec_type,
                    declared_duration=declared.declared_duration,
                    declared_packets=declared.declared_packets,
                    last_packet_pts=last,
                    observed_packets=None,
                    scanned_fully=False,
                )
            )
            continue

        # The tail is missing or short. Scan the whole stream to say how much is usable
        # -- a caller cannot bound its coverage from "something is wrong".
        full_last, full_count, full_stderr = _packet_timestamps(path, selector, None)
        stderr_blobs.append(full_stderr)
        observations.append(
            StreamObservation(
                index=declared.index,
                codec_type=declared.codec_type,
                declared_duration=declared.declared_duration,
                declared_packets=declared.declared_packets,
                last_packet_pts=full_last,
                observed_packets=full_count,
                scanned_fully=True,
            )
        )

    verdict = evaluate(container_duration, observations, tolerance)
    return replace(
        verdict,
        path=str(path),
        size_bytes=size_bytes,
        diagnostics=_collect_diagnostics(stderr_blobs),
    )


def clock(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS, for reading against a meeting's wall clock."""
    total = round(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def render(verdict: IntegrityVerdict) -> str:
    """Human-readable report. Leads with the verdict, then what is usable."""
    lines = [f"RECORDING INTEGRITY: {verdict.status.upper()}"]
    lines.append(f"  file     : {verdict.path}")
    if verdict.size_bytes is not None:
        lines.append(f"  size     : {verdict.size_bytes} bytes")
    lines.append(f"  declared : {verdict.declared_duration:.3f}s ({clock(verdict.declared_duration)})")
    lines.append(
        f"  usable   : {verdict.usable_duration:.3f}s ({clock(verdict.usable_duration)}) "
        f"= {verdict.usable_fraction * 100:.2f}% of declared"
    )
    if not verdict.ok:
        lines.append(
            f"  missing  : {verdict.missing_duration:.3f}s ({clock(verdict.missing_duration)}) "
            f"the container claims but does not contain"
        )
    lines.append("  streams:")
    for stream in verdict.streams:
        last = "none" if stream.last_packet_pts is None else f"{stream.last_packet_pts:.3f}s"
        counts = ""
        if stream.declared_packets is not None:
            counts = f" packets declared={stream.declared_packets}"
            if stream.observed_packets is not None:
                counts += f" observed={stream.observed_packets}"
        depth = "full scan" if stream.scanned_fully else "tail probe"
        lines.append(
            f"    {stream.codec_type[:1]}:{stream.index} {stream.codec_type} "
            f"declared={stream.declared_duration:.3f}s last-packet={last} "
            f"gap={stream.gap:.3f}s{counts} ({depth})"
        )
    if verdict.reasons:
        lines.append("  findings:")
        lines.extend(f"    {reason}" for reason in verdict.reasons)
    if verdict.diagnostics:
        lines.append("  decoder diagnostics:")
        lines.extend(f"    {line}" for line in verdict.diagnostics)
    if verdict.ok:
        lines.append(f"  safe to use the full {verdict.declared_duration:.3f}s for audio extraction and frame grids")
    else:
        lines.append(
            f"  ACTION: cap every frame grid and audio extraction at {verdict.usable_duration:.3f}s "
            f"({clock(verdict.usable_duration)}). Do not size coverage from the declared "
            f"{verdict.declared_duration:.3f}s -- {verdict.missing_duration:.3f}s of it does not exist."
        )
    return "\n".join(lines)


def as_dict(verdict: IntegrityVerdict) -> dict:
    """Machine-readable verdict, for a caller that computes its own frame bound."""
    return {
        "status": verdict.status,
        "path": verdict.path,
        "size_bytes": verdict.size_bytes,
        "declared_duration": round(verdict.declared_duration, 6),
        "usable_duration": round(verdict.usable_duration, 6),
        "missing_duration": round(verdict.missing_duration, 6),
        "usable_fraction": round(verdict.usable_fraction, 6),
        "tolerance": verdict.tolerance,
        "reasons": list(verdict.reasons),
        "diagnostics": list(verdict.diagnostics),
        "streams": [
            {
                "index": stream.index,
                "codec_type": stream.codec_type,
                "declared_duration": round(stream.declared_duration, 6),
                "last_packet_pts": stream.last_packet_pts,
                "gap": round(stream.gap, 6),
                "declared_packets": stream.declared_packets,
                "observed_packets": stream.observed_packets,
                "scanned_fully": stream.scanned_fully,
            }
            for stream in verdict.streams
        ],
    }


def exit_code(verdict: IntegrityVerdict, warn_only: bool = False) -> int:
    """Map a verdict to a process exit status.

    A truncated file REFUSES by default rather than warning. The failure mode is a tool
    reporting success while under-covering the recording, and a warning in that position
    is what went unnoticed once already. The measured separation between an intact file
    (0.064s) and the real defect (124s) leaves no plausible false positive, and
    `--warn-only` lets a caller proceed deliberately with the reported usable bound.
    """
    if verdict.status == STATUS_UNREADABLE:
        return EXIT_UNREADABLE
    if verdict.status == STATUS_TRUNCATED and not warn_only:
        return EXIT_TRUNCATED
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", help="path to the recording (mp4, mkv, anything ffprobe reads)")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE_SECONDS,
        help=(
            "seconds of declared-versus-payload disagreement to accept "
            f"(default: {DEFAULT_TOLERANCE_SECONDS}; largest gap measured on an intact recording was 0.064)"
        ),
    )
    parser.add_argument(
        "--tail-window",
        type=float,
        default=DEFAULT_TAIL_WINDOW_SECONDS,
        help=f"seconds before the declared end the fast pass seeks to (default: {DEFAULT_TAIL_WINDOW_SECONDS})",
    )
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON instead of prose")
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="report a truncated recording but exit 0, to proceed deliberately with the usable bound",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    path = Path(args.recording)
    if not path.is_file():
        parser.error(f"recording does not exist or is not a file: {path}")

    try:
        verdict = inspect(path, tolerance=args.tolerance, tail_window=args.tail_window)
    except ProbeError as exc:
        print(f"RECORDING INTEGRITY: UNREADABLE\n  {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    if args.json:
        print(json.dumps(as_dict(verdict), indent=2))
    else:
        print(render(verdict))

    code = exit_code(verdict, warn_only=args.warn_only)
    if verdict.status == STATUS_TRUNCATED and args.warn_only:
        print("  (--warn-only: exiting 0 despite the shortfall above)")
    return code


if __name__ == "__main__":
    sys.exit(main())
