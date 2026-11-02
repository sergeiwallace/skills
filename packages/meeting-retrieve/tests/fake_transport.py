"""A stand-in transport: answers each primitive from a JSON fixture and records its argv.

FAKE_TRANSPORT_FIXTURE names the fixture, FAKE_TRANSPORT_LOG the JSON-lines argv log.
Fixture keys: "chats" and "instances" (lists, or a string printed verbatim), and "land":
{"lines": [...], "exit": N}, each line formatted with {data}, {title} and {date}.
"""

import json
import os
import sys
from pathlib import Path


def option(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def emit(value):
    sys.stdout.write(value if isinstance(value, str) else json.dumps(value))
    sys.stdout.write("\n")


def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_TRANSPORT_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    fixture = json.loads(
        Path(os.environ["FAKE_TRANSPORT_FIXTURE"]).read_text(encoding="utf-8")
    )
    primitive = argv[0]
    if primitive == "chats":
        emit(fixture.get("chats", []))
        return fixture.get("chats_exit", 0)
    if primitive == "instances":
        rows = fixture.get("instances", [])
        day = option(argv, "--date")
        if day and isinstance(rows, list):
            rows = [r for r in rows if r["date"] == day]
        emit(rows)
        return 0
    land = fixture.get("land", {})
    day, title = option(argv, "--date"), option(argv, "--title")
    data = Path(option(argv, "--out-root")) / day / "data"
    for line in land.get("lines", []):
        print(line.format(data=data, title=title, date=day))
    return land.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main())
