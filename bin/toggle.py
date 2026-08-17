#!/usr/bin/env python3
"""Action entrypoint: open the hint overlay, or close it if it is already up.

Bound to a key via `type = "plugin_action"`, so the same keystroke toggles.
The focused pane id is captured here, before the overlay takes focus.
"""

import json
import os
import subprocess
import sys
import time

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or "/tmp"
STATE_PATH = os.path.join(STATE_DIR, "overlay.json")


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True)


def find_pane_id(payload):
    """Pull the first pane_id out of an arbitrarily shaped API response."""
    if isinstance(payload, dict):
        if isinstance(payload.get("pane_id"), str):
            return payload["pane_id"]
        for value in payload.values():
            found = find_pane_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_pane_id(item)
            if found:
                return found
    return None


def pane_exists(pane_id):
    return run([HERDR, "pane", "get", pane_id]).returncode == 0


def pane_rect(pane_id):
    """Size of the pane's content area, measured before the overlay covers it.

    herdr reports an open overlay as a synthetic split sibling, so the overlay
    cannot measure this for itself once it is up.
    """
    proc = run([HERDR, "pane", "layout", "--pane", pane_id])
    if proc.returncode != 0:
        return None
    try:
        layout = json.loads(proc.stdout)["result"]["layout"]
    except (ValueError, KeyError):
        return None
    for pane in layout.get("panes", []):
        if pane.get("pane_id") == pane_id:
            rect = pane.get("rect") or {}
            if rect.get("width") and rect.get("height"):
                return rect["width"], rect["height"]
    return None


def load_state():
    try:
        with open(STATE_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def clear_state():
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


def close_overlay(pane_id):
    """Dismiss the overlay by asking it to quit, not by closing its pane.

    herdr restores the pre-overlay focus and zoom when the overlay's process
    exits, and only while the overlay is still focused. `plugin pane close`
    strips the pane from the layout before that event lands, so the restore is
    skipped and focus ends up on a positional neighbour. esc is what hints.py
    already treats as dismiss. Wait for it to go so a fast second press cannot
    race a new overlay against the old one's exit.
    """
    run([HERDR, "pane", "send-keys", pane_id, "esc"])
    for _ in range(50):
        if not pane_exists(pane_id):
            break
        time.sleep(0.01)
    clear_state()


def main():
    state = load_state()
    if state and state.get("pane_id"):
        if pane_exists(state["pane_id"]):
            close_overlay(state["pane_id"])
            return 0
        clear_state()

    context = {}
    try:
        context = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        pass
    source = context.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID")
    if not source:
        print("link-hints: no focused pane in context", file=sys.stderr)
        return 1

    # Overlay panes always cover the active pane; passing --target-pane is rejected,
    # so the source id is handed to the overlay through the environment instead.
    argv = [
        HERDR, "plugin", "pane", "open",
        "--plugin", "linkhints",
        "--entrypoint", "hints",
        "--placement", "overlay",
        "--env", "LINKHINTS_SOURCE_PANE=%s" % source,
        "--focus",
    ]
    rect = pane_rect(source)
    if rect:
        argv += ["--env", "LINKHINTS_SOURCE_SIZE=%dx%d" % rect]
    opener = os.environ.get("LINKHINTS_OPENER")
    if opener:
        argv += ["--env", "LINKHINTS_OPENER=%s" % opener]

    proc = run(argv)
    if proc.returncode != 0:
        print("link-hints: open failed: %s" % proc.stderr.strip(), file=sys.stderr)
        return 1

    try:
        pane_id = find_pane_id(json.loads(proc.stdout))
    except ValueError:
        pane_id = None
    if pane_id:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_PATH, "w") as handle:
            json.dump({"pane_id": pane_id, "source_pane_id": source}, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
