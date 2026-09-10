# Meeting Analysis Scripts

Six Python scripts for analyzing meeting recordings, transcripts, and video frames.

## Scripts

In typical workflow order:

1. **recording_integrity.py** - Establish how many seconds of the recording actually exist before anything sizes work from its duration. It compares the container's declared duration against the last decodable packet of each stream and refuses a recording that claims payload it does not contain, reporting the usable bound so a frame grid can be capped honestly. **Run this first**: a truncated recording is otherwise discovered hours later, when an analyst notices the frames ran out.

2. **local_asr.py** - Run a local ASR (automatic speech recognition) pass over meeting audio using faster-whisper. Emits word-level JSONL with timing and confidence scores. Budget ~2.7x realtime on 4 CPU cores with `small.en`.

3. **transcript_diff.py** - Diff a vendor VTT transcript against the local ASR output to identify candidate mishearings. Vendor transcripts are accurate on ordinary speech and unreliable on product names; ASR-only terms at frequency 3+ are the useful signal.

4. **probe_terms.py** - Show vendor and local-ASR text side by side wherever suspect terms appear. Follow-up to `transcript_diff.py`: takes the candidate terms it identified and displays them in context from both sources, which is how you settle a mishearing.

5. **phash_distribution.py** - Calibration tool for `frame_dedup.py`. Computes the distribution of consecutive-frame phash distances at hash sizes 8 and 16, and shows how many frames various candidate thresholds would keep. Continuous console work can yield a unimodal distribution, meaning pixel-based deduplication cannot reliably detect "the screen changed" there.

6. **frame_dedup.py** - Deduplicate video frames by perceptual hash distance. Keeps a frame only when it differs from the last kept frame by at least a threshold. Useful for filtering a fixed-interval grid down to meaningful screen changes.

## Dependencies

Requirements:

- **ffprobe** - `recording_integrity.py`. It ships with ffmpeg and is that script's only dependency; no Python package is needed, so the integrity check runs even where the ASR and frame stack is not installed.
- **ffmpeg** - audio extraction (step 4) and frame grids (steps 5-6). Without it, transcript and notes-PDF analysis still work; ASR and frame analysis do not.
- **pdftotext** - the notes PDF (step 3).
- Python: **faster-whisper** (`local_asr.py`), **pillow** + **imagehash** + **numpy** (`frame_dedup.py`, `phash_distribution.py`).
- **tesseract** is deliberately NOT required. Native frame reading is more reliable for uncommon proper nouns, acronyms, jargon, and structured identifiers; keep OCR only as a cross-check for long literal identifiers.

### Automated setup (preferred)

```bash
MEETING_ANALYSIS_AUTO_INSTALL=1 bash "${CLAUDE_SKILL_DIR}/scripts/install_deps.sh"
```

Idempotent - safe to re-run after explicit opt-in. Without `MEETING_ANALYSIS_AUTO_INSTALL=1`, it reports missing dependencies and platform-specific manual commands, then exits without installing. With opt-in, it installs missing binaries (winget on Windows, apt where available), creates the venv, and installs the Python packages with `uv` (never `pip install`).

**Scripts verify dependencies.** `check_deps.sh` is the preflight shim: source it and call `meeting_analysis_preflight`. It invokes `install_deps.sh` only when something is missing and `MEETING_ANALYSIS_AUTO_INSTALL=1` has explicitly opted in.

```bash
source "${CLAUDE_SKILL_DIR}/scripts/check_deps.sh"
meeting_analysis_preflight --require-ffmpeg
meeting_analysis_preflight --transcript-only     # reports lost recording, ASR, and frame modalities
python3 "${CLAUDE_SKILL_DIR}/scripts/recording_integrity.py" "recording.mp4" || exit 1
"$MEETING_ANALYSIS_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/local_asr.py" audio.wav out.jsonl
```

The integrity check needs no venv - it imports only the standard library and shells out to `ffprobe` - so it runs before, and independently of, the Python toolchain the later steps need.

The happy path costs ~2s (an import check, no package-manager round trip). It verifies by **importing** rather than listing packages, because a present-but-broken wheel passes a list check. Verified against a positive control: uninstalling `imagehash` made the check fail, repair it, and re-verify.

Both scripts also adopt an already-installed-but-off-PATH binary - winget adds its shim to a per-user dir that an already-running shell has not picked up, so `ffmpeg` is frequently installed yet unresolvable.

### Environment overrides

| Variable | Default | Purpose |
|---|---|---|
| `MEETING_ANALYSIS_VENV` | `~/.cache/meeting-analysis-venv` | venv location |
| `MEETING_ANALYSIS_PYTHON` | set by full preflight | venv interpreter to run scripts with |
| `MEETING_ANALYSIS_AUTO_INSTALL` | unset | set to `1` to permit package installation |

The venv lives outside the repo on purpose: the tree's `.gitignore` covers `.venv/` only, so an in-tree `.venv-meeting-analysis/` would be committed.

**Platform note.** The installer uses winget when available on Windows and apt where available on Linux. It also finds winget-installed binaries that an already-running shell has not added to PATH.

The scripts import their heavy dependencies inside `main()` or after argparse so `--help` works even when the dependency is absent.

## Usage and Outputs

Every script accepts `--help` for full options. The scripts form a pipeline where outputs feed into later steps.

### 1. recording_integrity.py (run BEFORE anything reads the recording)

**What it protects against:** a recording whose container declares more duration than its payload contains. Every later step here sizes its coverage from the recording's duration, and `ffprobe`'s duration lies in the reassuring direction - it reports a clean, undamaged length whether or not the frames are there. A tool that trusts it under-covers the recording and reports success.

**Worked example:** a recording can declare 1667.2 seconds while its last decodable packet is at 1543.0 seconds. The missing portion is an upstream artifact to detect, never something to repair here; do not modify the recording.

**Invocation:**
```bash
python recording_integrity.py "recording.mp4"
python recording_integrity.py "recording.mp4" --json        # for a caller that computes its own bound
python recording_integrity.py "recording.mp4" --warn-only   # proceed deliberately with the usable bound
```

**Output:** a verdict, the usable bound, and per-stream evidence. A truncated recording can report:

```
RECORDING INTEGRITY: TRUNCATED
  declared : 1667.200s (27:47)
  usable   : 1543.000s (25:43) = 92.55% of declared
  missing  : 124.200s (2:04) the container claims but does not contain
  streams:
    v:0 video declared=1667.200s last-packet=1543.000s gap=124.200s packets declared=26676 observed=24689 (full scan)
    a:1 audio declared=1667.200s last-packet=1544.448s gap=122.752s packets declared=26050 observed=24133 (full scan)
  ACTION: cap every frame grid and audio extraction at 1543.000s (25:43). ...
```

**Exit status:** `0` intact, `1` truncated, `2` unreadable. It **refuses by default** rather than warning, because the failure mode is a tool reporting success while silently under-covering; `--warn-only` exits 0 so a pipeline can continue knowingly against the reported bound.

**Tolerance:** default 2.0s, `--tolerance` to override. An intact file always shows a small gap because a declared duration counts the final packet's own duration and a presentation timestamp does not. The default stays above an ordinary final packet interval while detecting a shortfall that could change frame coverage.

**Cost:** under a second on an intact 349 MB recording, because the first pass seeks into the last `--tail-window` seconds (default 30) rather than reading the file. A full sequential scan runs only for a stream that fails that pass, and only to quantify how much is missing.

**What to use:** the `usable` figure is the number to cap `ffmpeg -t` at, for both the audio extraction feeding `local_asr.py` and the frame grid feeding `phash_distribution.py` and `frame_dedup.py`. Do not derive frame counts from the declared duration on a file this refuses - that is the mistake it exists to stop.

---

### 2. local_asr.py

**Invocation:**
```bash
# Extract audio from video first (16kHz mono is plenty for ASR).
# Cap with -t at the usable duration recording_integrity.py reported, NOT the declared one.
ffmpeg -i recording.mp4 -t 1543.0 -ac 1 -ar 16000 -vn audio.wav

# Run ASR pass
python local_asr.py audio.wav asr-output.jsonl --model small.en --threads 4
```

**Output:** Writes a JSONL file (one JSON object per line), one object per ASR segment. Each segment contains:
- `start`, `end` - timing in seconds
- `text` - the transcribed segment text
- `avg_logprob`, `no_speech_prob` - segment-level confidence scores
- `words` - array of per-word objects with `w` (word), `s`/`e` (start/end), `p` (probability 0.0-1.0)

**What to use:** The per-word probabilities (`words[].p`) are what `transcript_diff.py` keys on for its low-confidence report. Words with `p < 0.35` indicate where both the vendor and ASR are unreliable.

---

### 3. transcript_diff.py

**Invocation:**
```bash
python transcript_diff.py vendor-transcript.vtt asr-output.jsonl --min-freq 3
```

**Output:** Prints three sections to stdout (writes no files):

1. **ASR-only terms** - Words that appear frequently in the ASR output but never in the vendor transcript. These are candidate vendor mishearings. **Start here.** An uncommon proper noun can appear as several plausible ordinary phrases in the vendor transcript while the independent ASR preserves a consistent candidate.

2. **Vendor-only terms** - Words the vendor transcript has that the ASR never heard. Usually the vendor is correct; occasionally an ASR miss.

3. **Low-confidence ASR words** - Timestamps where the ASR word probability is below the threshold (default 0.35). These mark spots where BOTH sources are unreliable, so a video frame or the recording itself is the only way to settle them.

Each section is frequency-sorted. Read section 1 first.

---

### 4. probe_terms.py

**Invocation:**
```bash
# Terms come from transcript_diff.py output (section 1: ASR-only terms)
python probe_terms.py vendor-transcript.vtt asr-output.jsonl --terms "mira grid" "mirror grade"
```

**Input dependency:** The suspect terms you pass with `--terms` come from `transcript_diff.py`'s ASR-only section. The two scripts are used in sequence, not independently.

**Output:** Prints paired excerpts to stdout for each occurrence of a suspect term:
```
[MM:SS] TERM='...' spk=Speaker
   VENDOR: <vendor transcript text at that timestamp>
   ASR   : <ASR text in a 24-second window around that timestamp>
```

Deduplicates to one hit per term per 30-second bucket, so repeated mentions in one exchange do not flood the output.

**What to use:** Reading the vendor and ASR text side by side is how you settle a mishearing. A vendor phrase beside a consistent ASR candidate in the same window is evidence to verify.

---

### 5. phash_distribution.py (run BEFORE frame_dedup.py)

**Invocation:**
```bash
# Extract a grid of frames first, e.g. one frame every 10 seconds.
# Cap with -t at the usable duration recording_integrity.py reported, NOT the declared one.
ffmpeg -i recording.mp4 -t 1543.0 -vf fps=1/10 derived/grid/g_%04d.jpg

# Compute distance distribution
python phash_distribution.py derived/grid/ --crop-fraction 0.865
```

**Output:** Prints to stdout (writes no files):
- Distance statistics at hash_size=8 and hash_size=16 (min, median, max, deciles)
- For each candidate threshold, how many frames it would keep

**What to use:** This is a calibration step. Run it to pick a threshold for `frame_dedup.py`. On a continuous-console recording it is expected to show a unimodal distribution (no clean "screen changed" signal), which tells you pixel-based deduplication will not work there. That negative result is the point: it tells you to anchor frame selection on transcript screen-share cues instead.

---

### 6. frame_dedup.py

**Invocation:**
```bash
# Use threshold from phash_distribution.py output
python frame_dedup.py derived/grid/ 60 --interval 10 --crop-fraction 0.865
```

**Output:** Prints to stdout (writes no files):
- Summary line: `candidates=N kept=M thr=T`
- One line per kept frame: `MM:SS<tab>seconds<tab>filename<tab>dist=D`

**What to use:** The timestamps (first column, or second column in seconds) are what you carry forward. These are the moments to sample full-resolution frames at for visual analysis. The filenames tell you which grid frame triggered each keep decision.

**Pipeline note:** On continuous console work, `phash_distribution.py` will likely tell you no threshold separates signal from noise. In that case, skip `frame_dedup.py` entirely and select frames based on the transcript's screen-share cues instead (see docs/meetings/README.md workflow step 5).

## See Also

- [the meeting workflow README](../../../docs/meetings/README.md) - the full meeting-analysis workflow these scripts support
