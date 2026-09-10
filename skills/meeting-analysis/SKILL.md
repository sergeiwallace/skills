---
name: meeting-analysis
description: Turn a zip or directory of meeting sources (recording, vendor transcript, notes export) into a template-conformant analysis doc. Cross-checks the vendor transcript against an independent local ASR pass to recover mangled product names, reads screen-share frames natively, and records what did not work. Use for "analyze this meeting" or "write up the standup".
license: Apache-2.0
argument-hint: <zip-or-directory> [--author NAME] [--date YYYY-MM-DD] [--series NAME] [--kind recording-analysis|standup-update]
model: inherit
---

# meeting-analysis

Turn whatever subset of meeting artifacts exists into a **citation-bearing analysis doc** that a
reader can audit against its sources.

**Usage:** `/meeting-analysis <zip-or-directory> [--author NAME] [--date YYYY-MM-DD] [--series NAME] [--kind recording-analysis|standup-update]`

The deterministic parts (which doc kind, where it goes, what each source is, whether one is already
there) are done by `meeting_resolve.py` so they are not re-derived by hand each meeting. The
judgment parts (reading frames, settling a mishearing, deciding what the meeting actually settled)
are yours. Do not hand the judgment to the script or the bookkeeping to yourself.

**Every command below is written against `${CLAUDE_SKILL_DIR}`, and that is not cosmetic.** This
skill runs from a symlinked skills farm, from a repo-local `.claude/skills`, or from a vendored
copy inside a consumer repo, so a repo-relative path such as `scripts/meeting_resolve.py` resolves
to the wrong file or to nothing at all depending on which copy is active and what the working
directory is. Use `${CLAUDE_SKILL_DIR}` for the scripts and the repo's own paths for the content.

## Which copy am I?

There can be two copies of this unit on one machine: the canonical one in the skill repo and a
vendored copy inside a repo that carries meeting docs of its own.

- **Templates are resolved from the CURRENT repo first.** `meeting_resolve.py plan` prefers the
  working repo's committed `docs/meetings/TEMPLATE.md` (or `docs/meetings/standups/TEMPLATE.md`),
  because that is the copy a teammate reading the repo on the web will open and the copy the doc's
  relative link points at. Only when the current repo has none does it fall back to the copy inside
  this skill's own repo, and it says which source it used.
- **When both exist and differ, `plan` reports `drifted: true`.** Say so in your report. A repo is
  allowed its own committed copy; a divergence nobody has noticed is how the two versions stop
  being the same document.
- **A vendored copy is verifiable.** A consumer repo carries `skills/vendor-lock.json` recording the
  source commit and a sha256 per file. Run
  `uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting_resolve.py" vendor-check` to confirm the copy
  is intact before trusting it; in the canonical checkout there is no lock and the command says so
  and exits 0.

## Pick the doc kind FIRST, and never bend one template into the other

Two kinds share the meetings tree. They have different sources, different purposes, and **their own
templates**. Conflating them is the known failure in this workflow.

| | Recording analysis | Standup update |
|---|---|---|
| Written | After the meeting | Before it |
| From | The recording, vendor transcript, notes export | Our own repo: git history, the task store, audit output |
| Answers | What the meeting settled, and what it did not | What we did since last time, and what is actually true now |
| Template | `docs/meetings/TEMPLATE.md` | `docs/meetings/standups/TEMPLATE.md` |
| `template_version` | `meeting-analysis-1.1.0` | `standup-update-2.3.0` |
| `meeting_type` | `standup` / `review` / `workshop` | `standup-update` |
| Slug | `<series>-standup-analysis-<date>` | `<series>-standup-update-<date>` |

**The discriminator is whether there is a recording or transcript to analyze.** The
recording-analysis template presupposes one: "What the recording shows that the transcript does
not", "Moments the frames do NOT corroborate" and "Terminology and transcription corrections" have
no meaning without it. **Filling those with "not applicable" means you picked the wrong kind.** A
standup update is instead written from our own repository, which is why its discipline is a
per-claim **evidence tier** (executed / read / asserted / blocked) rather than cross-source
checking: the tier is a required field, because the difference between a green unit suite and a
live verified path is one this workflow has gotten wrong before.

If a consumer has renamed either workflow, search its history using both the current and historical
spellings before assuming a document is absent. The public templates use only the names above.

## Step 1: preflight the sources, and let the script decide the bookkeeping

```bash
# A zip first lands in the meeting's data/ dir under its VENDOR filenames.
uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting_resolve.py" extract \
  "<archive.zip>" "docs/meetings/standups/<author>/<date>/data"

uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting_resolve.py" plan \
  --repo-root . --sources "docs/meetings/standups/<author>/<date>/data" \
  --author "<Name>" --date <YYYY-MM-DD> --series <series> --json
```

`plan` reports the kind and why, the resolved template and where it came from, every target path,
each source's role, and any source it cannot use. Read its output before writing anything.
`--author`, `--series` and the meeting-type group also come from `MEETING_ANALYSIS_*` environment
variables or from `~/.config/meeting-analysis/config.toml`, so a regular author sets them once
rather than per run; anything passed on the command line wins.

**The refusal it can return means stop rather than work around.** `CONFLICT: ... already exists`
(exit 2): a committed doc is **never renamed or duplicated** -- it is cited from design docs,
audits, other analyses and pull-request descriptions, several of them by line number. Refine the
existing doc in place, or fix the date or author you passed. Never invent a free filename.

## Step 2: the directory shape is a convention, not a preference

```text
docs/meetings/<meeting-type>/<author>/<YYYY-MM-DD>/
  <slug>-<YYYY-MM-DD>.md      the deliverable, at the date directory's root
  data/                       sources exactly as received from the vendor
  derived/                    everything generated from them
  frames/                     extracted video frames (git-ignored)
```

- **The `<author>` segment is kebab-case and is not optional.** It exists so two people can write
  for the same meeting on the same day without contending for one filename.
- **The date appears twice on purpose.** The directory needs it so a series sorts and so the doc
  sits beside its own `data/` and `derived/`. The filename needs it because the filename is what
  survives leaving the directory: in an editor tab, a link, a PDF export or an attachment, the
  parent directory is no longer visible, and a bare `standup.md` is ambiguous across a daily series.
- **Vendor filenames are never renamed**, so they stay traceable to the artifact received.

### Source-quoting hazard: these filenames WILL break an unquoted command

Example vendor filenames:

```text
[Team Sync] Weekly.vtt
[Team Sync] Weekly 2026-07-30.pdf
```

They contain **spaces and square brackets**, and brackets are glob metacharacters, so `[Team Sync]`
is read by the shell as a character class. Consequences:

- **Always double-quote every path**, including in `ffmpeg`, `pdftotext` and the analysis scripts:
  `ffmpeg -i "data/[Team Sync] Weekly.mp4" ...`.
- An unquoted `$FILE` does not merely mis-split on the space, it can expand to **nothing** and the
  command then reads stdin or errors on a missing operand. Both look like a tool problem.
- Prefer passing paths to the Python scripts, which take them as `argv` and never re-glob.
- Note the vendor is inconsistent: the transcript carries no date and the PDF does. Do not derive
  the meeting date from a filename -- pass `--date` explicitly.

## Step 3: run the analysis, one script per question

**Check the recording's integrity FIRST, before anything sizes work from its duration.** A
container can declare more duration than its payload contains, and `ffprobe`'s duration lies in the
reassuring direction, so every later step under-covers the recording and reports success. This
check needs only `ffprobe`, so it runs before and independently of the Python toolchain:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/recording_integrity.py" "data/<recording>.mp4" || exit 1
```

It exits 0 intact, 1 truncated, 2 unreadable, and prints the **usable** bound. Cap every `ffmpeg -t`
at that figure -- for the audio extraction feeding ASR and for the frame grid feeding the phash
steps -- and never at the declared duration on a file it refused. In one recording, a container
claimed 1667.2s while the last decodable packet was at 1543.0s: about two minutes did not exist.
Use `--warn-only` to proceed knowingly against the reported bound, and never modify the recording.

Then preflight the toolchain once. It verifies by **importing** rather than by listing packages (a
present-but-broken wheel passes a list check). Installation is opt-in: set
`MEETING_ANALYSIS_AUTO_INSTALL=1` only if you want it to run package-manager commands.

```bash
source "${CLAUDE_SKILL_DIR}/scripts/check_deps.sh"
meeting_analysis_preflight --require-ffmpeg
meeting_analysis_preflight --transcript-only  # reports lost recording/ASR/frame modalities
"$MEETING_ANALYSIS_PYTHON" "${CLAUDE_SKILL_DIR}/scripts/local_asr.py" ...
```

| Question | Script | What it gives you |
|---|---|---|
| Does the recording contain what it claims? | `recording_integrity.py` | the usable bound, and a refusal when it is short |
| What did an independent pass hear? | `local_asr.py` | word-level JSONL with per-word probabilities |
| Which terms did the vendor mangle? | `transcript_diff.py` | ASR-only / vendor-only / low-confidence reports |
| Which reading is right? | `probe_terms.py` | the two sources side by side at each hit |
| Is pixel-based frame selection viable here? | `phash_distribution.py` | the distance distribution, and what each threshold keeps |
| Which frames are worth reading? | `frame_dedup.py` | kept-frame timestamps, only if the above says a threshold works |

**Read `transcript_diff.py`'s ASR-only section first.** It earns the ASR pass: vendor transcripts
can render an uncommon proper noun as several plausible ordinary phrases while the local pass
preserves a consistent candidate. Feed those terms into `probe_terms.py` -- the two scripts are a
sequence, not alternatives. Log every correction in the doc's terminology table with the basis for
each.

Budget roughly **2.7x realtime** for ASR on 4 CPU cores with `small.en`.

### What prior negative results already rule out

These cost real time to obtain. Apply the lessons, and re-check them if the material differs.

- **Do not select frames by pixel change on continuous console or browser work.** A continuous
  screen share can yield a unimodal phash distribution rather than a usable "the screen changed"
  signal. Run
  `phash_distribution.py` to confirm for your material, then **anchor frame selection on the
  transcript's own screen-share cues** and skip `frame_dedup.py`. It stays useful for static
  presentations with clean transitions.
- **Read frames natively; do not rely on OCR.** OCR can miss uncommon proper nouns, acronyms,
  jargon and structured identifiers that direct reading recovers. Keep OCR as a cross-check for
  long literal identifiers only. `tesseract` is deliberately not a dependency.
- **A notes export can contain more than one meeting.** One export held two dates plus a follow-up
  table spanning both. Check the date headers before attributing any row.
- **`pdftotext -layout` misaligns table rows.** It emits wrapped row text *before* the row's number,
  so a leading-number scan attributes each row's tail to the next row's number and lands every
  assignee one row late. What works: strip the repeated print header first (it matches a bare-number
  scan itself), split on blank lines, then take the row number as the sole bare 1-2 digit token
  anywhere in the block and the assignee as any parenthesised name group anywhere in it. Assignees
  are a list, not a scalar, and some rows are narrative recap rather than tasks.
- **`ffmpeg -v error ... showinfo` prints nothing**, because `showinfo` logs at info level. Three
  scene-detection runs reported "0 changes at every threshold", a result indistinguishable from a
  working probe finding nothing. Use `-nostats -loglevel info`. Before reporting any command's
  output as a finding, state what that same command would print if your conclusion were false.

### When a dependency is missing

Degrade explicitly and say which modality you lost. Never let an absent tool read as an analyzed
source that found nothing.

| Missing | Still works | Lost |
|---|---|---|
| `ffprobe` | everything except the integrity check | the usable-duration bound, so cap nothing on trust |
| `ffmpeg` | transcript diffing, notes analysis | ASR (needs audio extraction) and every frame step |
| `pdftotext` | everything else | notes-export text; read the PDF directly instead |
| `faster-whisper` | vendor-transcript-only reading, notes | the independent pass, so no term rescue |
| `pillow` / `imagehash` | everything else | phash calibration and frame dedup |

`check_deps.sh` calls `install_deps.sh` only when something is actually missing **and**
`MEETING_ANALYSIS_AUTO_INSTALL=1` explicitly opts in. Otherwise it reports the gap and manual
commands without installing. If installation fails (no `winget`, no `apt`, no network), **record the gap in the doc's provenance section and mark
the affected findings uncorroborated** -- do not quietly narrow the analysis and present it as
complete. Both scripts also adopt an installed-but-off-PATH binary, because a winget shim
frequently has not propagated into an already-running shell, so `ffmpeg` is often installed yet
unresolvable.

**`uv` only. Never `pip install`** -- not in these scripts, not in a fix you write for them.

### Reporting a source you could not use

`plan` marks each unusable source with a reason (`.docx` has no extraction step, `.srt` is not
parsed, `.txt` is ambiguous). For every one, do both:

1. List it in the doc's **Provenance and method** table with what it is and that it was **not**
   used, rather than omitting it. A source silently dropped reads as one that was analyzed.
2. Say in your reply to the human which sources you could not consume and what would make them
   usable (export the recap as PDF, convert subtitles to WebVTT).

## Step 4: read the frames more than once, independently

Not optional for a meeting whose value is in the screen share. Independent reads can catch wrong
claims, but **agreement between reads is evidence, not proof** -- settle disagreements against the
repo, not against each other. Write each pass to its own
`derived/frame-analysis-*.md`, transcribe only literally legible text, and mark unreadable text
illegible rather than guessing.

## Step 5: synthesize into the resolved template

Fill the template `plan` resolved. Read it from disk at the start of the run rather than from memory
of it.

- Every quote **verbatim with its timestamp**, taken from `derived/transcript-extraction.md`.
- Where sources disagree, **say so** rather than silently picking one. Treat the notes export as
  untrusted: an AI-generated recap states decisions in a flatter, more confident register than the
  transcript supports.
- **Separate what the recording shows from what the transcript says.** Roughly a third of the first
  corpus's findings existed only in the video.
- **Mark anything not corroborated, and self-check that claim.** A timestamp is not visually
  confirmed when no frame was sampled there. The "Moments the frames do NOT corroborate"
  subsection is what makes the rest trustworthy.
- **Record the method, including what failed**, with measurements and wall-clock costs. That is the
  most reusable part of the document.
- Confirm no credentials appeared in any frame, and state where the git-ignored media lives.

## What is committed and what is not

Ignore by **FILE TYPE, never by directory**. Committed, because the analysis cites them:
`data/*.vtt`, `data/*.pdf`, `derived/*.jsonl`, `derived/frame-analysis-*.md`,
`derived/transcript-extraction.md`. Git-ignored: video and audio containers, `frames/`,
`derived/grid/`, `derived/ocr/`, and `derived/*.py`.

**Ignoring `data/` wholesale can keep a large video out while silently dropping its transcript.**
If a large binary needs excluding, add its extension. Never add a directory that also holds text
sources.

## Teammate-visible output

A meeting doc is usually read by people outside the session, so **ASCII only**, and reference issues
by their public tracker key only -- never an internal dev-tracking id. In a repo with a writing
standard for teammate-visible docs, read it before writing: answer first, numbered lists ordered by
impact, lead every item with what it functionally does rather than how it was done.

## Output checklist (before declaring done)

- [ ] `recording_integrity.py` run on the recording, and every `-t` capped at the usable bound it
      reported rather than at the declared duration.
- [ ] Kind chosen by the discriminator, and the other template's sections are absent rather than
      filled with "not applicable".
- [ ] `plan` run, its conflict line clear (or reported), and its `drifted` flag reported if set.
- [ ] Path is `<group>/<author>/<date>/<slug>-<date>.md`; no committed doc was renamed.
- [ ] Vendor filenames unchanged in `data/`; every path quoted in every command.
- [ ] `transcript_diff.py` ASR-only section read; each correction in the terminology table with its
      basis; unresolved terms marked Low confidence rather than guessed.
- [ ] Frame selection justified (calibration output, or transcript cues); frames read more than
      once; disagreements settled against the repo.
- [ ] Uncorroborated claims in their own subsection, self-checked against frames actually sampled.
- [ ] Every unused source listed in provenance with the reason.
- [ ] Method section records what failed, with measurements.
- [ ] ASCII only; no internal tracking ids; no credentials visible in any cited frame.

## Related

- `${CLAUDE_SKILL_DIR}/scripts/meeting_resolve.py` -- kind selection, path derivation, zip
  extraction, template resolution, vendored-copy verification.
- `${CLAUDE_SKILL_DIR}/scripts/README.md` -- per-script usage, outputs and measured costs.
- The working repo's `docs/meetings/README.md` -- the directory convention and the full operator
  workflow, including what is committed and what is ignored.
