---
name: teams-message-draft
description: Draft or revise a Microsoft Teams message in the user's own voice, in a rolling drafts doc per project, after reading the doc's voice guide and every AI-draft-versus-sent comparison in full. When the user says they sent a message, ask for a screenshot, transcribe it, write down what changed and grow the voice guide with citations; a script then archives the sent or dropped entry and validates the doc. Drafts into a file only and never posts. Use for "draft a Teams reply to the review thread" or "I sent the release note, here is the screenshot".
license: Apache-2.0
argument-hint: "<new <audience>: <what the message is for> | revise <entry> | sent <entry> | drop <entry>: <reason>>"
---

# teams-message-draft

`/teams-message-draft` writes Teams messages the user sends themselves, and learns their voice
from what they actually send. Every draft lives in one rolling drafts doc per project, shaped by
[templates/TEMPLATE.md](templates/TEMPLATE.md): pending entries first, and every sent or dropped
entry under "Archived Messages" > "Sent Messages" or "Dropped". Each entry keeps the AI draft
beside the message that was really sent, with notes on the differences, so the next draft starts
from the edits the user already made.

**Usage:**

```text
/teams-message-draft new <audience>: <what the message is for>
/teams-message-draft revise <entry heading or date>   (the user's feedback follows)
/teams-message-draft sent <entry heading or date>     (the user has sent it)
/teams-message-draft drop <entry heading or date>: <reason>
```

## This skill sends nothing

It edits a file and stops. Posting to Teams is the user's decision, and no agent message can stand
in for their say-so. The `teams` skill in this repository can post one message after the user
approves its exact text and destination; this skill never calls it.

## Quotes, mentions and the send

A reply quotes the message it answers with the Teams Reply feature and @mentions the person asked
to act, never a typed or styled quote. In a draft that means naming the answered message in
Metadata (its sender, its time, its first words) and writing `@Name` where the mention goes, with
no quote in the draft text: the user makes the quote by choosing Reply on that message, and types
the `@` to make a mention that notifies. Settle the text before any send, since Teams can refuse to
edit a long posted message ("Failed to edit. Message too large."). Any message an agent sends is
read back afterwards and recorded here like any other sent message.

## The script

`scripts/teams_drafts.py` owns everything about the doc's layout that can be computed. Run it with
Python 3.11 or later:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/teams_drafts.py" <command> [options]
```

| Command | What it does |
|---|---|
| `resolve` | Prints the drafts repo, doc and screenshots folder from the config (flags > env > `[profiles.<name>]` > `[defaults]`). |
| `set-status <key> Sent --sent "<when>"` or `<key> Dropped --note "<reason>"` | Flips one entry's heading tag and sets its Metadata Status, Last updated and Sent rows. |
| `archive` | Moves every entry into the region its tag names, newest first, rebuilds the table of contents and the tracking table, validates the result, and writes only if it passes. |
| `validate [FILE]` | Exits 1 and names each offending entry: a bad marker, a Sent or Dropped entry outside the archive, a Pending entry inside it, an AI-generated draft wrapped whole in a code block or blockquote, a TOC or tracking row that disagrees. |

The config lives at `~/.config/teams-message-draft/config.toml`; copy
[config.example.toml](config.example.toml) there and name your project's repo and doc.
`TEAMS_DRAFTS_CONFIG` names another file, and `--project`, `--repo`, `--file`, `--profile` (or
`TEAMS_DRAFTS_PROJECT`, `TEAMS_DRAFTS_REPO`, `TEAMS_DRAFTS_FILE`, `TEAMS_DRAFTS_PROFILE`) override
it per call.

`set-status` and `archive` take `--repo <worktree>` and refuse a repository's main working tree or
any tree with uncommitted or untracked files. That refusal is what makes a bad run revertible with
`git restore` alone, so never work around it: commit first.

## Steps

1. Resolve the drafts doc with `teams_drafts.py resolve`. Create a git worktree of that repo and
   edit only there, never in its main working tree. When the project has no drafts doc, create it
   from `templates/TEMPLATE.md`, add the project to the config file, and run `archive` once to
   build its generated regions. A new project's doc may start from another project's voice guide,
   keeping each citation as a link to the file it came from.
2. Read the doc's "Voice and style guide" and every "Notes on the differences" subsection in full,
   oldest to newest, archived entries included. Do this before every draft and every edit, even
   when you read them earlier in the session: each sent message can add a lesson.
3. For `new`: add an entry at the top of the pending entries, right after the `pending-messages`
   marker and a `---` line, headed `## [Pending] YYYY-MM-DD - <audience>: <subject>`. Give it the
   four subsections in order, each heading ending in the entry key `(YYYY-MM-DD <short-slug>)`:
   Metadata (Status, Drafted, Last updated, Sent, Channel / audience, Related stories, Screenshot,
   then a sentence or two on why the message exists), AI-generated draft, Message actually sent
   (`Not sent yet.`) and Notes on the differences (`Not sent yet.`). Write the draft as plain text
   directly under its heading, one paragraph per line, with no greeting or sign-off and never
   wrapped in a code fence or a blockquote: the user copies it straight into Teams, and the wrapper
   comes along with it. A code block that is part of the message, such as a command for the reader
   to run, is not a wrapper. For a reply, name the message it answers in Metadata and type no
   quote. Commit, then run `archive` to add its TOC lines and tracking row.
4. For `revise`: rewrite the AI-generated draft from the user's feedback, quote that feedback in
   the entry's notes subsection (it is voice data too), and bump Last updated.
5. For `sent`: ask the user for a screenshot of the message as it appears in Teams, unless they
   already shared one in this conversation. Never reconstruct the sent text from the draft or from
   memory; the screenshot shows what the draft cannot (link text, mentions, paragraph breaks, the
   send time, an Edited marker). Save it in the screenshots folder `resolve` names, transcribe it
   into "Message actually sent" by the conventions below, set the Screenshot row, and write
   "Notes on the differences". Promote each reusable lesson into the voice guide by the citation
   rule below. Then archive it (step 7) with `set-status <key> Sent --sent "<date and time shown>"`.
   With no screenshot, write `Not captured` and say in the notes that no comparison was possible.
6. For `drop`: record the reason, then archive it with `set-status <key> Dropped --note "<reason>"`.
7. To archive a sent or dropped entry, run this loop:
   1. Commit every pending change in the drafts worktree first, screenshots included.
   2. `set-status`; read the diff and commit it.
   3. `archive --repo <worktree>`, then `validate`.
   4. Read the diff yourself. The entry must sit under Sent Messages or Dropped, whole, with its
      TOC lines and tracking row moved with it and nothing else changed. The validator is code
      too and can be wrong, so its PASS does not replace this read.
   5. If either check fails, revert the run with `git -C <worktree> restore -- <doc>`, fix the
      script with a failing test first, and rerun from step 3 until both checks agree.
   6. Commit the archive run.
8. Check the hard constraints over any draft you wrote: ASCII only; the team's public tracker keys
   and never a private tracking id; no email addresses; every web URL the message references as a
   hyperlink with readable display text, inline, never a bare URL, never the URL as its own text
   and never in backticks. Where you can, have a model that did not write the draft review it.
   Commit the drafts doc under its repo's rules and tell the user the entry is ready, with the
   doc's absolute path and the entry heading.

When the user says in passing that they sent a drafted message, step 5 applies at once: ask for
the screenshot in the same reply.

## Transcription conventions

| What the screenshot shows | How to write it |
|---|---|
| A Teams Reply quote (a quote card above the message that links to the original) | `> **Sender as shown:** quoted text` as the first block, and a sentence before the transcription saying it is a Reply quote of that sender's message |
| A Teams mention (a name in link colour) | `@Name` |
| A chip Teams rendered as code, such as a group name | inline code |
| A hyperlink | `[display text](url)` with the display text exactly as shown; the URL from the draft or the linked doc, else the display text followed by `(target not visible)` |
| Paragraph breaks | one paragraph per line, the same breaks as the screenshot |
| Wording, case, punctuation, typos | exactly as sent, uncorrected |
| Curly quotes and apostrophes | their straight ASCII forms, the only change allowed |
| Send time and an Edited marker | in Metadata, Sent field, for example `2026-01-02 9:46 AM (shown as Edited)` |

## The citation rule

Each guideline in the voice guide's "Learned from sent messages" list ends with a citation link to
the notes subsection that produced it, for example
`Source: [2026-01-02 release note, notes](#notes-on-the-differences-2026-01-02-release-note)`.
That lets anyone open the AI draft and the sent message behind a guideline.

The subsection headings carry the entry key so the citations hold: GitHub numbers repeated
headings `-1`, `-2` in file order, so a bare `### Notes on the differences` anchor would move every
time a new entry is added at the top. The key also keeps the tag out of those anchors, so flipping
`[Pending]` to `[Sent]` breaks no citation, and the move into the archive breaks none either.

Guideline numbers never change. Append a new guideline with the next number; retire one by
replacing its text with `Retired YYYY-MM-DD: <reason>`. When a later message confirms an existing
guideline, add its citation to that guideline instead of writing a second one. In the notes, mark a
promoted lesson with `Promoted to the voice guide as guideline N.` A convention the user set
directly, with no sent message behind it, goes under "House rules" marked `(house rule)`.

## Anti-patterns

- Drafting from memory of the voice guide. It changes with every sent message; read it again.
- Filling "Message actually sent" from the draft because the user said "sent it as is". Ask for
  the screenshot; small edits are the ones that teach the most.
- Writing a guideline with no citation. If no sent message backs it, it is a house rule.
- Renumbering the voice guide, or moving an entry or editing the generated regions by hand.
- Imitating a quote with a `>` line, a `<blockquote>` or a bold sender name.
- Taking the validator's PASS as the check, or working around a refusal by stashing or
  discarding changes.
