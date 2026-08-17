#!/usr/bin/env python3
"""Overlay entrypoint: redraw the source pane and paint hint labels over its links.

Runs inside a herdr plugin pane with placement = "overlay", so it covers the pane
whose links we are labelling. The source pane id arrives via LINKHINTS_SOURCE_PANE
(set by bin/toggle.py through `herdr plugin pane open --env`).

Keys: a hint label opens the link, the same label uppercased copies it instead,
esc / q / ctrl-c dismisses.
"""

import json
import os
import re
import subprocess
import sys
import termios
import tty
import unicodedata

IS_MAC = sys.platform == "darwin"

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
SOURCE_PANE = os.environ.get("LINKHINTS_SOURCE_PANE") or ""
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or ""

# Home row first, so the common case is the least finger travel.
ALPHABET = "asdfghjklqwertyuiopzxcvbnm"

# Trailing punctuation is almost always sentence punctuation, not part of the URL.
TRAILING = ".,;:!?)]}>\"'`"

URL_RE = re.compile(
    r"(?:https?://|ftp://|file://|git://|ssh://|mailto:|www\.)"
    r"[^\s<>\"'`\x00-\x1f\x7f]+"
)
SGR_RE = re.compile(r"\x1b\[[0-9;:?]*m")

LABEL_SGR = "\x1b[1;38;5;16;48;5;220m"  # bold black on amber
RESET = "\x1b[0m"


def cell_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s):
    return sum(cell_width(c) for c in s)


def read_pane(pane_id):
    """Return the visible screen of pane_id as (ansi_lines, plain_lines)."""
    proc = subprocess.run(
        [HERDR, "pane", "read", pane_id, "--source", "visible", "--format", "ansi"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return [], []
    ansi_lines = proc.stdout.decode("utf-8", "replace").split("\n")
    plain_lines = [SGR_RE.sub("", line) for line in ansi_lines]
    return ansi_lines, plain_lines


def find_links(plain_lines, max_rows, max_cols):
    """Locate links in the grid. Returns [(row, col, url)], reading order."""
    hits = []
    for row, line in enumerate(plain_lines[:max_rows]):
        for match in URL_RE.finditer(line):
            url = match.group(0).rstrip(TRAILING)
            if not url:
                continue
            # A trailing ')' is part of the URL when the link itself opened one.
            if match.group(0)[len(url):].startswith(")") and url.count("(") > url.count(")"):
                url += ")"
            col = display_width(line[: match.start()])
            if col >= max_cols:
                continue
            hits.append((row, col, url))
    return hits


def left_trim_ansi(line, drop):
    """Drop `drop` display columns from the left, keeping SGR state intact."""
    if drop <= 0:
        return line
    kept_state = []
    out = []
    width = 0
    i = 0
    while i < len(line):
        match = SGR_RE.match(line, i)
        if match:
            (out if width >= drop else kept_state).append(match.group(0))
            i = match.end()
            continue
        ch = line[i]
        if width >= drop:
            out.append(ch)
        width += cell_width(ch)
        i += 1
    return "".join(kept_state) + "".join(out)


def make_labels(count):
    if count <= len(ALPHABET):
        return [ALPHABET[i] for i in range(count)]
    labels = []
    for first in ALPHABET:
        for second in ALPHABET:
            labels.append(first + second)
            if len(labels) == count:
                return labels
    return labels


def render(ansi_lines, hints, rows, cols, dx, dy):
    out = ["\x1b[?7l", "\x1b[?25l", "\x1b[0m\x1b[2J"]  # no autowrap, no cursor, clear
    for row, line in enumerate(ansi_lines):
        target = row - dy
        if 0 <= target < rows:
            out.append("\x1b[%d;1H\x1b[0m%s" % (target + 1, left_trim_ansi(line, dx)))
    for label, (row, col, _url) in hints:
        col -= dx
        if col + len(label) > cols:
            col = max(0, cols - len(label))
        out.append("\x1b[%d;%dH%s%s%s" % (row - dy + 1, col + 1, LABEL_SGR, label, RESET))
    if not hints:
        note = " no links on this screen — press any key "
        out.append("\x1b[%d;1H%s%s%s" % (rows, LABEL_SGR, note[:cols], RESET))
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def configured_opener():
    """Optional override: {"opener": "/path/to/program"} in the plugin config dir."""
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not config_dir:
        return None
    try:
        with open(os.path.join(config_dir, "config.json")) as handle:
            value = json.load(handle).get("opener")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) and value else None


def open_url(url):
    opener = os.environ.get("LINKHINTS_OPENER") or configured_opener()
    if opener:
        argv = [opener, url]
    else:
        argv = ["open", url] if IS_MAC else ["xdg-open", url]
    with open(os.devnull, "wb") as devnull:
        subprocess.Popen(argv, stdout=devnull, stderr=devnull, start_new_session=True)


def copy_url(url):
    candidates = (["pbcopy"],) if IS_MAC else (["wl-copy"], ["xclip", "-selection", "clipboard"])
    for argv in candidates:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, start_new_session=True)
        except FileNotFoundError:
            continue
        proc.communicate(url.encode("utf-8"))
        return


def clear_state():
    if not STATE_DIR:
        return
    try:
        os.remove(os.path.join(STATE_DIR, "overlay.json"))
    except OSError:
        pass


def close_self():
    """Restore the terminal and let the process exit; exiting *is* the close.

    herdr only puts the pre-overlay focus and zoom back when the overlay's own
    process dies, and only while the overlay is still the focused pane. Closing
    the pane from in here with `plugin pane close` removes it from the layout
    first, so that restore is skipped and focus lands on a positional neighbour.
    """
    clear_state()
    sys.stdout.write("\x1b[?7h\x1b[?25h\x1b[0m")
    sys.stdout.flush()


def read_key(fd):
    ch = os.read(fd, 1)
    if not ch:
        return None
    if ch == b"\x1b":  # swallow any escape sequence, treat as dismiss
        return "\x1b"
    try:
        # Assemble a full UTF-8 codepoint; labels are ASCII but the user may type anything.
        extra = 0
        b = ch[0]
        if b >= 0xF0:
            extra = 3
        elif b >= 0xE0:
            extra = 2
        elif b >= 0xC0:
            extra = 1
        while extra:
            ch += os.read(fd, 1)
            extra -= 1
        return ch.decode("utf-8", "replace")
    except OSError:
        return None


def main():
    if not SOURCE_PANE:
        close_self()
        return 0

    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        rows, cols = size.lines, size.columns
    except OSError:
        rows, cols = 24, 80

    # herdr draws pane chrome around the overlay, so its interior is inset inside the
    # pane it covers. Cancel that out, otherwise every line appears nudged diagonally.
    dx = dy = 0
    size = os.environ.get("LINKHINTS_SOURCE_SIZE") or ""
    if "x" in size:
        try:
            src_cols, src_rows = (int(part) for part in size.split("x", 1))
            dx = max(0, (src_cols - cols) // 2)
            dy = max(0, (src_rows - rows) // 2)
        except ValueError:
            pass

    ansi_lines, plain_lines = read_pane(SOURCE_PANE)
    links = [
        hit for hit in find_links(plain_lines, len(plain_lines), cols + dx)
        if 0 <= hit[0] - dy < rows and hit[1] >= dx
    ]
    hints = list(zip(make_labels(len(links)), links))
    by_label = {label: hit[2] for label, hit in hints}

    render(ansi_lines, hints, rows, cols, dx, dy)

    fd = sys.stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        close_self()
        return 0

    typed = ""
    try:
        tty.setraw(fd)
        while True:
            key = read_key(fd)
            if key is None or key in ("\x1b", "\x03", "\x07"):
                break
            if not hints:
                break
            if key in ("\x7f", "\x08"):
                typed = typed[:-1]
                continue
            if key in ("q", "Q") and not typed:
                break
            copy = key.isupper()
            typed += key.lower()
            if typed in by_label:
                url = by_label[typed]
                if url.startswith("www."):
                    url = "https://" + url
                if copy:
                    copy_url(url)
                else:
                    open_url(url)
                break
            if not any(label.startswith(typed) for label in by_label):
                typed = ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    close_self()
    return 0


if __name__ == "__main__":
    sys.exit(main())
