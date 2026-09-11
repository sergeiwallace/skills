---
name: shortcuts
description: Print an OS-aware cheatsheet of Claude Code prompt-box editing shortcuts
license: Apache-2.0
---

# shortcuts

Print the keyboard shortcuts for navigating and editing the CC prompt box.
Shortcuts are also shown in the rotating statusline tips — this skill gives the full list on demand.

**Usage:** `/shortcuts`

## Behavior

1. Detect the host OS: check `AI_HOST` env var (`mac` = macOS, `linux` = Linux, `windows` = Windows). Fall back to `uname -s` if `AI_HOST` is unset.
2. Print the appropriate cheatsheet from the table below. Always include the "CC commands" section — those are OS-independent.

## Cheatsheet

### macOS

| Action | Shortcut |
| :----- | :------- |
| Start / end of line | `Ctrl+A` / `Ctrl+E` |
| Word backward / forward | `Opt+←` / `Opt+→` |
| Delete prev word | `Ctrl+W` |
| Delete to start / end of line | `Ctrl+U` / `Ctrl+K` |
| New line in prompt | `Shift+Enter` |
| Cancel input / interrupt Claude | `Ctrl+C` |
| Clear screen | `Ctrl+L` |
| History prev / next | `↑` / `↓` |
| Search history | `Ctrl+R` |

### Linux

| Action | Shortcut |
| :----- | :------- |
| Start / end of line | `Ctrl+A` / `Ctrl+E` |
| Word backward / forward | `Alt+←` / `Alt+→` (or `Ctrl+←` / `Ctrl+→`) |
| Delete prev word | `Ctrl+W` |
| Delete to start / end of line | `Ctrl+U` / `Ctrl+K` |
| New line in prompt | `Shift+Enter` |
| Cancel input / interrupt Claude | `Ctrl+C` |
| Clear screen | `Ctrl+L` |
| History prev / next | `↑` / `↓` |
| Search history | `Ctrl+R` |

### Windows

| Action | Shortcut |
| :----- | :------- |
| Start / end of line | `Ctrl+A` / `Ctrl+E` (Git Bash readline) |
| Word backward / forward | `Alt+←` / `Alt+→` (Git Bash) |
| Delete prev word | `Ctrl+W` |
| Delete to start / end of line | `Ctrl+U` / `Ctrl+K` |
| New line in prompt | `Shift+Enter` |
| Cancel input / interrupt Claude | `Ctrl+C` |
| Clear screen | `Ctrl+L` |
| History prev / next | `↑` / `↓` |
| Search history | `Ctrl+R` |

### CC commands (all platforms)

| Action | Command / Key |
| :----- | :------------ |
| Accept suggestion | `Tab` |
| Accept suggestion + run | `Tab Tab` |
| Accept edit diffs | `Shift+Tab` |
| Vim mode | `/vim` |
| Switch model | `/model` |
| Compact context | `/compact` |
| Clear context | `/clear` |
| View costs | `/cost` |
| View memories | `/memory` |
| View hooks | `/hooks` |
| All shortcuts | `?` |

## Notes

- Bindings are readline-based and depend on the terminal emulator and shell.
- On macOS, the VS Code terminal maps `Option+←/→` to word-nav by default; configure in VS Code Keyboard Shortcuts if it doesn't work.
- `/shortcuts` prints the list for your current `AI_HOST`. To see a different OS's list, pass the OS name: `/shortcuts linux`, `/shortcuts mac`, `/shortcuts windows`.
