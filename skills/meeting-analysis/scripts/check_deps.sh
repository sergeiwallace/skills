#!/usr/bin/env bash
# Dependency preflight for the meeting-analysis scripts.
#
# Purpose: verify the toolchain before analysis. Source this, then call
# `meeting_analysis_preflight`; it runs install_deps.sh only when dependencies
# are missing *and* MEETING_ANALYSIS_AUTO_INSTALL=1 explicitly permits it.
#
# Usage from a wrapper or by hand:
#   source "$(dirname "$0")/check_deps.sh"
#   meeting_analysis_preflight            # full toolchain
#   meeting_analysis_preflight --transcript-only
#   "$MEETING_ANALYSIS_PYTHON" local_asr.py ...
#
# Exports on success: MEETING_ANALYSIS_PYTHON (venv interpreter), and prepends the
# ffmpeg directory to PATH when it was found off-PATH.
set -uo pipefail

MEETING_ANALYSIS_VENV="${MEETING_ANALYSIS_VENV:-$HOME/.cache/meeting-analysis-venv}"

_ma_have() { command -v "$1" >/dev/null 2>&1; }

_ma_venv_python() {
    local c
    for c in "$MEETING_ANALYSIS_VENV/Scripts/python.exe" "$MEETING_ANALYSIS_VENV/bin/python"; do
        [ -x "$c" ] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

# Add a winget-installed binary's directory to PATH when the shim has not
# propagated into this already-running shell.
_ma_adopt_winget_bin() {
    local name=$1 base found
    base="${LOCALAPPDATA:-$HOME/AppData/Local}/Microsoft/WinGet/Packages"
    [ -d "$base" ] || return 1
    found=$(find "$base" -iname "$name" -type f 2>/dev/null | head -1)
    [ -n "$found" ] || return 1
    PATH="$PATH:$(dirname "$found")"; export PATH
}

# True when the whole toolchain is usable, so we can skip the installer.
_ma_deps_ok() {
    local py
    py=$(_ma_venv_python) || return 1
    "$py" -c "import faster_whisper, PIL, imagehash, numpy" >/dev/null 2>&1 || return 1
    if [ "${_MA_REQUIRE_FFMPEG:-0}" = "1" ]; then
        _ma_have ffmpeg || _ma_adopt_winget_bin ffmpeg.exe || return 1
    fi
    return 0
}

_ma_report_degraded_modes() {
    local missing=() py
    _ma_have ffprobe || missing+=("ffprobe (usable-duration bound unavailable)")
    _ma_have ffmpeg || missing+=("ffmpeg (ASR and frame analysis unavailable)")
    _ma_have pdftotext || missing+=("pdftotext (notes-PDF text extraction unavailable)")
    py=$(_ma_venv_python) || py=""
    if [ -z "$py" ] || ! "$py" -c "import faster_whisper" >/dev/null 2>&1; then
        missing+=("faster-whisper (independent ASR unavailable)")
    fi
    if [ -z "$py" ] || ! "$py" -c "import PIL, imagehash" >/dev/null 2>&1; then
        missing+=("pillow/imagehash (phash calibration and frame dedup unavailable)")
    fi
    if [ ${#missing[@]} -gt 0 ]; then
        printf 'preflight: degraded mode; lost: %s\n' "${missing[*]}" >&2
    fi
}

meeting_analysis_preflight() {
    _MA_REQUIRE_FFMPEG=0
    local transcript_only=0
    while [ $# -gt 0 ]; do
        case $1 in
            --require-ffmpeg) _MA_REQUIRE_FFMPEG=1 ;;
            --transcript-only) transcript_only=1 ;;
            *) printf 'preflight: unknown option %s\n' "$1" >&2; return 2 ;;
        esac
        shift
    done

    # Pick up already-installed-but-off-PATH binaries before deciding anything.
    _ma_have ffmpeg    || _ma_adopt_winget_bin ffmpeg.exe    >/dev/null 2>&1 || true
    _ma_have pdftotext || _ma_adopt_winget_bin pdftotext.exe >/dev/null 2>&1 || true

    if [ "$transcript_only" = "1" ]; then
        _ma_report_degraded_modes
        printf 'preflight: transcript-only mode; usable: vendor-transcript reading and available notes analysis\n' >&2
        return 0
    fi

    if ! _ma_deps_ok; then
        if [ "${MEETING_ANALYSIS_AUTO_INSTALL:-0}" != "1" ]; then
            printf 'preflight: dependencies incomplete; automatic installation is disabled. Set MEETING_ANALYSIS_AUTO_INSTALL=1 to opt in, or run install_deps.sh for manual commands.\n' >&2
            _ma_report_degraded_modes
            return 1
        fi
        printf 'preflight: dependencies incomplete -- running install_deps.sh (opted in)\n' >&2
        local here; here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
        bash "$here/install_deps.sh" >&2 || {
            printf 'preflight: install_deps.sh FAILED\n' >&2; return 1
        }
        # Re-adopt: the installer may have put binaries somewhere new.
        _ma_have ffmpeg || _ma_adopt_winget_bin ffmpeg.exe >/dev/null 2>&1 || true
        _ma_deps_ok || { printf 'preflight: still incomplete after install\n' >&2; return 1; }
    fi

    MEETING_ANALYSIS_PYTHON=$(_ma_venv_python) || return 1
    export MEETING_ANALYSIS_PYTHON
    return 0
}
