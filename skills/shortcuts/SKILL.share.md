---
name: shortcuts
description: Show portable keyboard and shell shortcuts for the current operating system.
---

# shortcuts

Detect the operating system at runtime and print only shortcuts that apply to it. Do not infer a
machine profile or hostname.

## Behavior

1. Run `uname -s` when available.
2. Treat `Darwin` as macOS, `Linux` as Linux, and `MINGW*`, `MSYS*`, or `CYGWIN*` as Windows Git Bash.
3. If `uname` is unavailable, report that the platform is unknown and show the all-platform section.

## Cheatsheet

### macOS

- `Cmd-K`: clear the terminal viewport.
- `Cmd-Shift-P`: open the editor command palette.
- `pbcopy` / `pbpaste`: copy or paste through the system clipboard.

### Linux

- `Ctrl-Shift-C` / `Ctrl-Shift-V`: terminal copy and paste in common desktop terminals.
- `xclip -selection clipboard` or `wl-copy`: clipboard integration when installed.
- `Ctrl-R`: reverse-search shell history.

### Windows Git Bash

- `Ctrl-Insert` / `Shift-Insert`: terminal copy and paste.
- Use POSIX paths inside Git Bash and `cygpath -w` only when a native Windows command needs one.
- Quote paths that may contain spaces.

### CC commands (all platforms)

- `/help`: show available commands.
- `/compact`: compact the conversation when context is high.
- `/clear`: start a fresh conversation.
- `Ctrl-C`: cancel the active operation.

## Notes

Shortcuts vary by terminal and editor. Prefer the host application's documented binding when it
conflicts with this portable baseline.
