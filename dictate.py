#!/usr/bin/env python3
"""
Scribe Dictate — terminal version (handy for debugging).
For everyday use, run the menu-bar app: ./build_app.sh

Hold the hotkey (default: Right Option), speak, release → text is pasted at the cursor.
Esc cancels · Ctrl+C quits.
"""
import sys

import Quartz

import scribe_core as core


def main() -> None:
    engine = core.Engine()
    perms = core.permissions(prompt=True)
    if not perms["accessibility"]:
        core.log("⚠ Accessibility not granted yet (needed to paste) — approve it, then restart the terminal.")

    if not engine.install_tap(Quartz.CFRunLoopGetCurrent()):
        sys.exit(
            "✖ Could not listen to the keyboard.\n"
            "  Grant Input Monitoring to your terminal app:\n"
            "  System Settings → Privacy & Security → Input Monitoring,\n"
            "  then quit and reopen the terminal."
        )

    action = "Hold" if core.MODE == "hold" else "Press"
    core.log(f"Scribe Dictate ready · {engine.describe()}")
    core.log(f"{action} [{core.HOTKEY}] to dictate · Esc cancels · Ctrl+C quits")
    try:
        while True:
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.5, False)
    except KeyboardInterrupt:
        core.log("Bye 👋")


if __name__ == "__main__":
    main()
