---
name: teams
description: Read Microsoft Teams chats, meeting threads, transcripts (.vtt), recordings and Loop notes with the Teams web client's own tokens from a token provider you plug in, land a named meeting's artefacts (the latest, or a date and time) in a dated folder from a named-meetings config, read a meeting Recap's AI summary from a Teams tab you keep open, and send a Teams message only after you approve its exact text and destination. Use for "read the team chat", "get the stand-up transcript" or "download the meeting recording and notes".
license: Apache-2.0
argument-hint: "<meeting NAME [--date D] [--time HH:MM] | chats | messages | thread | vtt | loop-links | recording-uris | instances | land | recap-summary | send> [options]"
---

# teams

Reads Microsoft Teams with the four tokens the Teams web client itself uses, and posts one message once the account owner has approved it. The tokens come from a token provider you choose in the settings file. The chat service and the media gateway it reads are the endpoints the Teams web client calls; this route works where Microsoft Graph's meeting scopes need admin consent.

**Before you use it**, check that reading your organisation's Teams through the web client's own endpoints is allowed by your organisation's policy. Those endpoints are undocumented and can change without notice.

Run every command through `uv`, from any directory:

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/teams.py" <command> [options]
```

`teams.py` uses only the Python standard library (3.11 or later). `--help` and a send preview never load a token. `meeting.py` also needs the repository's `packages/skill-cli-foundation` and `packages/meeting-retrieve`, which `uv run --script` resolves from a checkout of this repository.

## Before the first call: tokens and settings

Copy [config.example.toml](config.example.toml) to `~/.config/teams-skill/teams.toml` and edit the copy. `TEAMS_CONFIG` names another file in the same shape, `teams.py --config` overrides both, and `TEAMS_PROFILE=<name>` applies a `[profiles.<name>]` table over `[defaults]`. The file is read on every call, so an edit applies to the next command. Set at least `login_hint` (what to run when a token has expired), `timezone` and `title`; the `[meetings.<name>]` tables are the named meetings `meeting.py` reads.

The script needs four tokens and the chat-service region:

| Field | Audience | Used by |
| --- | --- | --- |
| `chat_service` | the regional Teams chat service (`skypetoken`) | `chats`, `messages`, `thread`, every dated command, `send` |
| `ic3` | the async media gateway | the `.vtt` fallback route |
| `graph` | `graph.microsoft.com` | Loop links, recording download, Loop HTML and PDF |
| `sharepoint` | the tenant's SharePoint | the primary `.vtt` route |
| `region` | not a token: the chat-service region, for example `amer` or `emea` | every chat-service URL |

Never print, cat or copy a token value, and never read the token file or a broker's cache yourself. The script redacts every value the provider returned from all of its own output.

If a command exits 4 or prints `not authenticated`, run the command that `login_hint` names, then retry.

### Plugging in a token broker

The script never mints tokens. A broker that already signs in as the Teams web client plugs in one of two ways:

1. **The broker writes a token file.** Keep `provider = "json-file"` and point `[defaults.json-file].path` at a file the broker refreshes. The file is one JSON object with the keys `chat_service`, `ic3`, `graph`, `sharepoint` and `region`; without `region`, the config's `region` is used, and with neither every call to Teams exits 3. Keep it outside every git work tree, mode `600`.
2. **An adapter module.** Set `provider = "<module>:<callable>"` and, if the module is not importable already, `provider_path` to its directory. The callable takes the merged settings mapping and returns a mapping with the same five keys. Raise an exception with a short message when the broker holds no valid tokens: the script reports it as not authenticated and exits 4. The message passes through the redacting writer, but never put a token in it. An adapter that wraps a broker's own Python API keeps the broker's cache format private to the adapter.

A missing or empty token makes only the commands that need it fail, so a broker that cannot mint, for example, the SharePoint audience still serves the gateway `.vtt` route.

## Read commands

`--tz` defaults to the config's `timezone`; dates are local dates in that zone, so an evening message stored as the next UTC day still counts as the meeting day. The chat service returns one page of at most 200 messages; when the requested date is older than everything fetched, the script warns instead of reporting the day empty.

| Command | What it returns |
| --- | --- |
| `chats [--query Q] [--limit N]` | conversation id, kind and topic of each chat whose topic contains Q |
| `messages --conv ID [--limit N]` | the conversation as text, newest first |
| `thread --conv ID [--date YYYY-MM-DD] [--json]` | id, local time, sender, message type and excerpt per message, optionally one local date |
| `vtt --conv ID --date D --out DIR [--title T] [--force]` | writes that date's transcript to `DIR/<title> <date>.vtt` and names the route that answered. Refuses to overwrite without `--force` |
| `loop-links --conv ID --date D` | each Loop link posted that date, decoded to its inner `:fl:` URL and drive and item ids, then resolved through Graph `/shares/.../driveItem`. Every failure prints its status |
| `recording-uris --conv ID --date D` | the gateway object ids and `uri=` values of that date's recording messages, each classified by host |
| `chats --query Q --json` | the matching chats as a JSON list of `{conv, topic}` |
| `instances --conv ID [--date D] [--tz TZ] --json` | each recording in the chat, newest first: local `date` and start `time`, `start_utc`, `recording` name, `message_id` |
| `land --conv ID --date D [--time HH:MM] --title T --out-root R [--ai-summary FILE] [--force]` | lands one meeting instance's artefacts (below); a day with several recordings needs `--time` |

`vtt` tries the recording's OneDrive transcript first: the `onedriveForBusinessTranscript` item in the recording message, read at `.../streamContent?is=1&applymediaedits=false` with the SharePoint token and its UTF-8 BOM stripped. That is the file the Teams client's "Download as .vtt" gives. If it fails, the script reads the gateway's `views/transcript` with the IC3 token and warns, because that view merges each speaker's consecutive cues and drops the cue ids.

## A meeting's artefacts in one command

Common meetings get names in the `[meetings.<name>]` tables of the settings file, read on every call:

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting.py" team-standup                     # the newest one
uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting.py" team-standup --date 2026-10-19 --time 14:00
uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting.py" --list
uv run --script "${CLAUDE_SKILL_DIR}/scripts/meeting.py" --chat "[Team] Review" --date 2026-10-19 --root meetings/reviews
```

`meeting.py` parses its options through the shared skill CLI library (`packages/skill-cli-foundation`) and runs the shared named-meetings front end (`packages/meeting-retrieve`), which drives `teams.py` through three commands: `chats --json` to resolve a chat title, `instances --json` to list a chat's recordings, and `land` to fetch one instance. It picks the instance this way:

1. No `--date`: the newest recording in the chat.
2. A date with one recording: that one.
3. A date with two or more: it refuses (exit 2) and lists each with its local start time. `--time HH:MM` picks the one starting within 45 minutes of it; an entry's `time` key is a default `--time`. Two or none within the window is refused the same way. On such a day the transcript, the Loop HTML, the notes PDF and the AI Summary inbox file carry the instance's start after the date (`<title> <date> HHMM.vtt`), and the recording keeps its OneDrive name, which already differs per recording, so two instances never overwrite each other.

It writes into `<repo>/<root>/<date>/data/`, or `<repo>/<root>/<slug>-<date>/data/` for an ad hoc meeting landed with `--conv` or `--chat`. The repository is `--repo-path`, else the current checkout when its origin name matches the entry's `repo`, else the directory with that name beside the current repository's main checkout, else it refuses naming `--repo-path`. A session working in a git worktree passes `--repo-path <its worktree>`. A configured meeting always lands in its entry's `repo`: with a NAME, a `--repo` or a `--repo-path` into any other repository is refused (exit 2). `--repo NAME` chooses the repository only for an ad hoc meeting. The output is always a git checkout: a directory that is not inside one, or that falls outside `allowed_repos` or `meetings_prefix` when they are set, is refused (exit 2), so artefacts are never left in a scratch directory.

The AI Summary text comes, in order, from the Loop page itself, `--ai-summary FILE`, or the inbox file `data/<title> <date> AI summary.txt` (`data/<title> <date> HHMM AI summary.txt` on a day with several instances). With none, `land` writes the notes-only PDF and prints `AI Summary: MISSING`, and `meeting` adds an `AI Summary request:` line built from `[defaults] ai_summary_request` and an `AI Summary inbox:` line, then exits 1 (on stderr when `--json` is given). Drop the file at the inbox path and run the same command again; nothing downloads twice.

`--json` prints one object for a caller such as `/meeting-analysis --retrieve`: `meeting`, `date`, `time`, `conv`, `title`, `repo_path`, `meeting_dir`, `data_dir`, `artefacts`, `ai_summary`, `complete`, `missing`, `meeting_type`, `series`, `author`, `slug`, `ad_hoc`.

The meetings config: `[defaults]` may set `tz`, `author`, `ai_summary_request` (placeholders `{chat}`, `{date}`, `{title}`, `{inbox}`), `allowed_repos` (the only repositories a meeting may land in) and `meetings_prefix` (the directory every meeting must land under, for example `docs/meetings`); unknown keys there are ignored, because the table is shared with this skill's settings. Each `[meetings.<name>]` table takes `chat` and/or `conv` (`conv` wins), `title`, `repo`, `root`, `meeting_type`, `series`, and optionally `time`, `tz`, `aliases` and `slug` (a template containing `{date}`); an unknown key there exits 3 naming it. `--config FILE` reads another file after this one.

### Landing one instance directly

`land` is the transport command `meeting.py` calls, usable on its own once you know the conversation:

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/teams.py" land --conv "$CONV" --date 2026-10-19 --title "[Team] Stand-up" --out-root meetings/standups
```

It writes into `<out-root>/<date>/data/`, keeping any file already there unless `--force`:

1. `<title> <date>.vtt`, through the same routes as `vtt`.
2. The recording under its OneDrive name. The recording message names its drive and item ids (`onedriveForBusinessVideo`), and Graph `/content` downloads it with the Graph token; the size is checked against the drive item. The script refuses to write it anywhere git would track it, so add the folder's media pattern to the ignore file first.
3. `<title> <date>.html`: the meeting's Loop notes page, converted by Graph `content?format=html`.
4. `<title> <date>.pdf`: the notes with an H1 `summary_heading` section just before `anchor_heading` (defaults "AI Summary" and "Follow-up tasks"):
   - If the Loop page already has that heading, Graph `content?format=pdf` is the deliverable.
   - If not, pass `--ai-summary FILE`, the Recap window's AI summary tab text pasted as plain text with `-` bullets and two-space indents for sub-bullets. The script inserts the section into a local copy of the HTML, keeps it in `<out-root>/<date>/derived/`, and prints that copy to PDF with local headless Chromium (Playwright's, found automatically, or `--chromium PATH`) with network lookups blocked. The Loop page itself is never edited.
   - With neither, it writes `<title> <date> (no AI Summary).pdf`, prints `AI Summary: MISSING` and exits 1. Re-run with `--ai-summary FILE`; files already there are kept, so nothing downloads again.

It exits 0 only when every artefact is present and the PDF carries the summary section.

The Graph API for the Recap AI summary needs a consented scope and a Copilot licence, so `recap-summary` (below) reads that text from a Teams tab the owner keeps open. Copying it from the Recap window by hand into the Loop page or the `--ai-summary` file still works.

## The Recap AI summary from an open Teams tab

`recap-summary` reads a meeting's Recap "AI summary" tab from Teams web in a Chromium-family browser you started with `--remote-debugging-port` and signed in to Teams. It needs no token:

```bash
uv run --script "${CLAUDE_SKILL_DIR}/scripts/teams.py" recap-summary --port 9222 --chat "[Team] Stand-up" --date 2026-10-19 --out "/tmp/[Team] Stand-up 2026-10-19 AI summary.txt"
```

It writes the text in the `--ai-summary` format: a `Meeting notes` block and a `Follow-up tasks` block, `-` bullets, two-space indents for sub-bullets. Without `--out` it prints the text; it never overwrites a file unless `--force`. Write it to the inbox path `meeting.py` printed (`data/<title> <date> AI summary.txt`, with ` HHMM` after the date on a day with several instances) and run the same `meeting.py` command again, or pass it to `land --ai-summary`.

What it does, and all it does:

1. Finds the page whose host is in `recap_tab_hosts` (default `teams.cloud.microsoft`) on the CDP port (`--port`, else the config's `cdp_port`; with neither it exits 3), and brings it to the front of its window (`Target.activateTarget`). With several, it uses the first. With none, it opens `recap_open_url` (default `https://teams.cloud.microsoft/`) in a new tab, waits for it to load, and when the read ends leaves that tab on `recap_park_url` (default `chrome://newtab/`). It never closes a tab, so it can never close the window's last one. It refuses any CDP method outside a fixed list (attach, detach, activate, create, navigate for the parking step only, evaluate, and the Network reads for `--collab-needle`).
2. Opens the chat `--chat` from Teams' left navigation unless the tab already shows it (`[data-tid=chat-title]`), and refuses (exit 2) when the chat list has no row of that name.
3. Scrolls the chat's message list up until the meeting card for that date (the long form, `October 19, 2026`) with a `View recap` button renders, clicks that button, then clicks the `AI summary` tab.
4. Expands each notes row by its chevron, and never clicks a row's timestamp button. It reads the `Meeting notes` treegrid and the `Follow-up tasks` tree, each found from its H2, never from the first tree on the page (Teams' left navigation is one). A row whose text is only a number is a count badge and is dropped.

The tab is brought to the front because a tab that is not in front paints no frames, and Teams finishes a chat switch on a frame. If the browser window is minimized, the tab never becomes visible and the command stops with that cause. Each run leaves the tab on the Recap view; the next run reopens the chat from the left navigation.

`--collab-needle TEXT` also enables the Network domain on the tab and, for the `readcollabobject` response Recap fetches, prints its status, its top-level JSON keys and whether it contains TEXT. It never prints the body, a header, a token or a cookie.

## Sending a message: HARD HUMAN GATE, every time

**A message is sent only after the account owner has approved, in chat, the exact message text and the exact destination, and that approval covers that one message.** It does not carry over to a second message, an edited body or another chat. No other write exists: no edit, reply, reaction, chat creation or file upload.

1. Write the HTML body to a file. It posts as `RichText/Html`. **Every web URL the message references (an issue, a web page, a repository or a file, a wiki page) goes in as a hyperlink with readable display text, `<a href="https://...">the issue key and title</a>`, never as a bare URL and never with the URL as its own text.** The preview refuses a body that breaks it, naming each URL; `--allow-bare-urls` posts it anyway when the owner wants it as written.
2. Preview it. This posts nothing and loads no token:

   ```bash
   uv run --script "${CLAUDE_SKILL_DIR}/scripts/teams.py" send --conv "$CONV" --file /tmp/msg.html
   ```

   It prints the destination, the exact body and the body's sha256.
3. Show the owner that destination and body verbatim and wait for approval of that message.
4. Send with the sha256 from the preview:

   ```bash
   uv run --script "${CLAUDE_SKILL_DIR}/scripts/teams.py" send --conv "$CONV" --file /tmp/msg.html --send --sha256 <hex>
   ```

   The script refuses if the file's sha256 differs, so what goes out is byte-identical to what was approved. It posts once.

Rehearse with `--to-notes` in place of `--conv`, which posts to the owner's own Saved Messages (`48:notes`). A rehearsal is still a send and needs the same approval.

This send has no native Reply quote and no after-send readback. Never imitate a quote with a `<blockquote>`, and read the message back with `thread` after posting, checking that every approved link came back as a link and not as text or a bare URL. Settle the text before the send: Teams can refuse to edit a long posted message ("Failed to edit. Message too large."). The `teams-message-draft` skill in this repository drafts the text in your voice first.

In a session that runs without permission prompts, back this gate with a permission `ask` rule naming the send flag, for example `Bash(*teams.py send *--send*)` in the project's settings; Claude Code does not auto-approve a call matched by an explicit ask rule.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | done |
| 1 | the call ran and failed, or found nothing for that date |
| 2 | refused: bad arguments, an existing file, or a send without the approved sha256 |
| 3 | config: the settings file or the token provider could not be loaded (the message names it) |
| 4 | not authenticated: run the command `login_hint` names |

## Maintenance

The chat-service and gateway endpoints are undocumented and can change without notice; their URL templates are settings (`chat_service_url`, `gateway_url`, `graph_url`) with code defaults in `DEFAULTS`. Run the tests from the repository root with `make test`, or only this skill's:

```bash
make test PYTEST_ARGS="tests/test_teams_skill.py tests/test_teams_recap_summary.py tests/test_teams_meeting_wrapper.py"
```

`recap-summary`'s selectors were measured on the live Teams web client on 2026-10-20 and live in `scripts/recap_summary.js`. Its tests drive a real headless Chromium against a fixture page of that structure (`tests/fixtures/teams_recap/chat.html`) and skip where no Chromium-family browser is installed.
