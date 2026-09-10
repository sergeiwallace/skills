#!/usr/bin/env bash
# Install / verify everything the meeting-analysis scripts need.
#
# Idempotent after explicit opt-in: set MEETING_ANALYSIS_AUTO_INSTALL=1 before
# running this script. Without that variable it reports missing dependencies and
# manual commands, then exits without changing the machine.
#
# Platform note: Linux systems commonly use apt; Windows systems commonly use
# winget. A winget-installed binary can be present but absent from an already
# running shell's PATH, so both paths are handled below.
set -uo pipefail

VENV_DIR="${MEETING_ANALYSIS_VENV:-$HOME/.cache/meeting-analysis-venv}"
STAMP="$VENV_DIR/.deps-ok"
# The Python package list deliberately lives in ONE place only: the import->package
# mapping in the heredoc below. A parallel shell array cannot express that mapping
# (faster-whisper imports as faster_whisper, pillow as PIL) and would drift from it.

log()  { printf '  %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- binaries ----
# Resolve a winget-installed exe even when its shim is not on PATH: winget adds
# the alias to a per-user dir that an already-running shell has not picked up.
find_winget_exe() {
    local name=$1
    local base="${LOCALAPPDATA:-}/Microsoft/WinGet/Packages"
    [ -d "$base" ] || base="$HOME/AppData/Local/Microsoft/WinGet/Packages"
    [ -d "$base" ] || return 1
    find "$base" -iname "$name" -type f 2>/dev/null | head -1
}

manual_binary_command() {  # $1=winget id  $2=apt package
    local wid=$1 apt=$2
    if have winget; then
        printf 'winget install --id %s --accept-source-agreements --accept-package-agreements' "$wid"
    elif have apt-get; then
        printf 'sudo apt-get install -y %s' "$apt"
    else
        printf 'install the required package with your system package manager'
    fi
}

report_missing_without_installing() {
    local missing=0 py packages
    printf 'install_deps: automatic installation is disabled. Set MEETING_ANALYSIS_AUTO_INSTALL=1 to opt in.\n' >&2
    if ! have ffmpeg && ! find_winget_exe ffmpeg.exe >/dev/null 2>&1; then
        printf 'missing: ffmpeg\nmanual: %s\n' "$(manual_binary_command Gyan.FFmpeg ffmpeg)" >&2
        missing=1
    fi
    if ! have pdftotext && ! find_winget_exe pdftotext.exe >/dev/null 2>&1; then
        printf 'missing: pdftotext\nmanual: %s\n' "$(manual_binary_command Xpdf.XpdfReader poppler-utils)" >&2
        missing=1
    fi
    py=""
    for c in "$VENV_DIR/Scripts/python.exe" "$VENV_DIR/bin/python"; do
        [ -x "$c" ] && { py=$c; break; }
    done
    if [ -z "$py" ]; then
        printf 'missing: meeting-analysis Python environment and packages\nmanual: uv venv %s --python 3.12 && uv pip install --python %s faster-whisper pillow imagehash numpy\n' "$VENV_DIR" "$VENV_DIR/bin/python" >&2
        missing=1
    else
        packages=$("$py" - <<'PY' 2>/dev/null
mods = {"faster_whisper": "faster-whisper", "PIL": "pillow", "imagehash": "imagehash", "numpy": "numpy"}
missing = []
for mod, pkg in mods.items():
    try:
        __import__(mod)
    except Exception:
        missing.append(pkg)
print(" ".join(missing))
PY
)
        if [ -n "${packages// /}" ]; then
            printf 'missing: Python packages %s\nmanual: uv pip install --python %s %s\n' "$packages" "$py" "$packages" >&2
            missing=1
        fi
    fi
    return "$missing"
}

ensure_binary() {  # $1=command  $2=winget id  $3=apt package
    local cmd=$1 wid=$2 apt=$3
    if have "$cmd"; then log "$cmd: present"; return 0; fi

    local found
    found=$(find_winget_exe "$cmd.exe" 2>/dev/null)
    if [ -n "$found" ]; then
        log "$cmd: installed but not on PATH -> $(dirname "$found")"
        PATH="$PATH:$(dirname "$found")"; export PATH
        return 0
    fi

    log "$cmd: MISSING -- installing"
    if have winget; then
        winget install --id "$wid" --accept-source-agreements \
            --accept-package-agreements --disable-interactivity >/dev/null 2>&1
    elif have apt-get; then
        sudo apt-get install -y "$apt" >/dev/null 2>&1
    else
        log "$cmd: no winget or apt available -- install manually"
        return 1
    fi

    found=$(find_winget_exe "$cmd.exe" 2>/dev/null)
    [ -n "$found" ] && { PATH="$PATH:$(dirname "$found")"; export PATH; }
    have "$cmd" || [ -n "$found" ] || { log "$cmd: install FAILED"; return 1; }
    log "$cmd: installed"
}

if [ "${MEETING_ANALYSIS_AUTO_INSTALL:-0}" != "1" ]; then
    if report_missing_without_installing; then
        log "all dependencies already present; no installation needed"
        exit 0
    fi
    exit 1
fi

# ffmpeg is required (audio extraction + frame grids). tesseract is optional --
# the workflow reads frames natively because OCR missed most domain terms; it is
# kept only as a cross-check for long literal identifiers.
ensure_binary ffmpeg   Gyan.FFmpeg          ffmpeg   || FFMPEG_FAILED=1
ensure_binary pdftotext Xpdf.XpdfReader     poppler-utils || log "pdftotext: optional-ish, needed for the notes PDF"

# ------------------------------------------------------------------ python ----
PYTHON_BIN=""
for c in "$VENV_DIR/Scripts/python.exe" "$VENV_DIR/bin/python"; do
    [ -x "$c" ] && { PYTHON_BIN=$c; break; }
done

if [ -z "$PYTHON_BIN" ]; then
    log "venv: creating at $VENV_DIR"
    if have uv; then
        uv venv "$VENV_DIR" --python 3.12 >/dev/null 2>&1
    else
        # uv is the mandated package manager fleet-wide; fall back only to bootstrap it.
        python -m venv "$VENV_DIR" >/dev/null 2>&1
    fi
    for c in "$VENV_DIR/Scripts/python.exe" "$VENV_DIR/bin/python"; do
        [ -x "$c" ] && { PYTHON_BIN=$c; break; }
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    log "venv: FAILED to create at $VENV_DIR"
    exit 1
fi
log "venv: $PYTHON_BIN"

# Check imports rather than `pip list`: an import is the thing the scripts
# actually do, and a present-but-broken wheel passes a list check.
missing=$("$PYTHON_BIN" - <<'PY' 2>/dev/null
mods = {"faster_whisper": "faster-whisper", "PIL": "pillow",
        "imagehash": "imagehash", "numpy": "numpy"}
out = []
for mod, pkg in mods.items():
    try:
        __import__(mod)
    except Exception:
        out.append(pkg)
print(" ".join(out))
PY
)

if [ -n "${missing// /}" ]; then
    log "python packages missing:$missing -- installing"
    # uv ONLY (never pip install) per the repo's mandatory package-manager rule.
    if have uv; then
        uv pip install --python "$PYTHON_BIN" $missing >/dev/null 2>&1
    else
        log "uv not found -- cannot install (pip install is forbidden in this repo)"
        exit 1
    fi
else
    log "python packages: all present"
fi

# Re-verify after install; never stamp on an unverified state.
if ! "$PYTHON_BIN" -c "import faster_whisper, PIL, imagehash, numpy" >/dev/null 2>&1; then
    log "python packages: verification FAILED after install"
    exit 1
fi
log "python packages: verified"

if [ "${FFMPEG_FAILED:-0}" = "1" ] && ! have ffmpeg; then
    log ""
    log "WARNING: ffmpeg unavailable. ASR (step 4) and frame work (steps 5-6) cannot run;"
    log "         transcript + notes-PDF analysis still can."
fi

printf '%s\n' "deps verified" > "$STAMP"
log ""
log "Done. Activate with:  export PATH=\"\$PATH:$(dirname "${PYTHON_BIN}")\""
log "Or let each script auto-resolve it via check_deps.sh."
