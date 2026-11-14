#!/usr/bin/env python3
"""
Scribe Dictate — menu-bar app.

Launched by "Scribe Dictate.app" (see build_app.sh), or directly for testing:
    .venv/bin/python menubar.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import rumps
import Quartz
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

import scribe_core as core

ICONS = {
    "idle": "🎙",
    "recording": "🔴",
    "transcribing": "⏳",
    "error": "⚠️",
    "paused": "⏸",
}

LANGUAGES = [
    ("Auto-detect", None, []),
    ("Ελληνικά + English", "el", ["en"]),
    ("English only", "en", []),
    ("Ελληνικά μόνο", "el", []),
]

HOTKEY_LABELS = {
    "right_option": "Right ⌥", "left_option": "Left ⌥",
    "right_command": "Right ⌘", "left_command": "Left ⌘",
    "right_control": "Right ⌃", "left_control": "Left ⌃",
    "right_shift": "Right ⇧", "left_shift": "Left ⇧",
    "fn": "fn 🌐",
}

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?"
PERMISSION_PANES = [
    ("Microphone", SETTINGS_URL + "Privacy_Microphone"),
    ("Input Monitoring", SETTINGS_URL + "Privacy_ListenEvent"),
    ("Accessibility", SETTINGS_URL + "Privacy_Accessibility"),
]


def _open(*args: str) -> None:
    subprocess.Popen(["/usr/bin/open", *args])


class ScribeMenuBar(rumps.App):
    def __init__(self, engine: core.Engine):
        super().__init__("Scribe Dictate", title=ICONS["idle"], quit_button=None)
        self.engine = engine
        self.hotkey_label = HOTKEY_LABELS.get(core.HOTKEY, core.HOTKEY.upper())
        self.tap_ok = engine.install_tap(Quartz.CFRunLoopGetMain())
        self.perms = core.permissions(prompt=True)

        # --- menu ---------------------------------------------------------- #
        self.status_item = rumps.MenuItem("Starting…")
        self.pause_item = rumps.MenuItem("Pause dictation", callback=self.toggle_pause)

        self.recent_menu = rumps.MenuItem("Recent dictations")
        self.recent_menu.add(rumps.MenuItem("(none yet)"))

        self.lang_menu = rumps.MenuItem("Language")
        self.lang_items = []
        for label, code, secondary in LANGUAGES:
            item = rumps.MenuItem(label, callback=self._language_cb(code, secondary))
            self.lang_menu.add(item)
            self.lang_items.append((item, code, secondary))

        perm_menu = rumps.MenuItem("Permissions")
        for name, url in PERMISSION_PANES:
            perm_menu.add(rumps.MenuItem(f"Open {name} settings…", callback=lambda _, u=url: _open(u)))

        self.menu = [
            self.status_item,
            None,
            self.pause_item,
            self.recent_menu,
            self.lang_menu,
            None,
            rumps.MenuItem("Edit settings (.env)…", callback=lambda _: _open("-t", str(core.ENV_FILE))),
            rumps.MenuItem("Edit keyterms…", callback=self.edit_keyterms),
            rumps.MenuItem("Open log", callback=lambda _: _open(str(core.LOG_FILE))),
            perm_menu,
            None,
            rumps.MenuItem("Restart (apply settings)", callback=self.restart),
            rumps.MenuItem("Quit Scribe Dictate", callback=rumps.quit_application),
        ]
        self._sync_language_checks()

        self._shown_title = None
        self._shown_status = None
        self._shown_history = -1
        self._ticks = 0
        self.timer = rumps.Timer(self.refresh, 0.2)
        self.timer.start()

        action = "Hold" if core.MODE == "hold" else "Press"
        core.log(f"Menu-bar app ready · {engine.describe()} · {action} [{core.HOTKEY}]")
        if not self.tap_ok:
            core.log("⚠ Input Monitoring not granted yet — waiting for it…")

    # ---- callbacks -------------------------------------------------------- #
    def toggle_pause(self, _sender) -> None:
        self.engine.set_enabled(not self.engine.enabled)
        self.pause_item.title = "Pause dictation" if self.engine.enabled else "Resume dictation"

    def _language_cb(self, code, secondary):
        def _cb(_sender):
            self.engine.set_language(code, secondary)
            self._sync_language_checks()
        return _cb

    def _sync_language_checks(self) -> None:
        cur_code = self.engine.scribe.language_code
        cur_sec = self.engine.scribe.secondary
        for item, code, secondary in self.lang_items:
            item.state = 1 if (code == cur_code and (not code or secondary == cur_sec)) else 0

    def edit_keyterms(self, _sender) -> None:
        if not core.KEYTERMS_FILE.exists():
            example = core.CONFIG_DIR / "keyterms.example.txt"
            core.KEYTERMS_FILE.write_text(example.read_text(encoding="utf-8") if example.exists() else "", encoding="utf-8")
        _open("-t", str(core.KEYTERMS_FILE))

    def restart(self, _sender) -> None:
        core.log("↻ Restarting…")
        os.execv(sys.executable, [sys.executable, "-u", os.path.abspath(__file__)])

    def _copy_cb(self, text: str):
        def _cb(_sender):
            core.copy_to_clipboard(text)
        return _cb

    # ---- UI refresh (main thread) ----------------------------------------- #
    def refresh(self, _timer) -> None:
        self._ticks += 1
        if self._ticks % 15 == 0:  # every ~3s: re-check permissions, retry the hotkey listener
            self.perms = core.permissions()
            if not self.tap_ok:
                self.tap_ok = self.engine.install_tap(Quartz.CFRunLoopGetMain())
                if self.tap_ok:
                    core.log("✔ Input Monitoring granted — hotkey active")

        status = self.engine.status
        if not self.tap_ok:
            title, text = ICONS["error"], "⚠ Grant Input Monitoring (Permissions menu)"
        elif status == "paused":
            title, text = ICONS["paused"], "Paused"
        elif status == "recording":
            title, text = ICONS["recording"], "● Recording…  (Esc cancels)"
        elif status == "transcribing":
            title, text = ICONS["transcribing"], "Transcribing…"
        elif status == "error":
            title, text = ICONS["error"], f"⚠ {self.engine.last_error[:70]}"
        elif not self.perms["accessibility"]:
            title, text = ICONS["error"], "⚠ Grant Accessibility to auto-paste"
        else:
            verb = "Hold" if core.MODE == "hold" else "Press"
            title, text = ICONS["idle"], f"Ready · {verb} {self.hotkey_label} to dictate"

        if title != self._shown_title:
            self.title = title
            self._shown_title = title
        if text != self._shown_status:
            self.status_item.title = text
            self._shown_status = text

        if self.engine.history_version != self._shown_history:
            self._shown_history = self.engine.history_version
            self.recent_menu.clear()
            history = list(self.engine.history)
            if not history:
                self.recent_menu.add(rumps.MenuItem("(none yet)"))
            for i, text in enumerate(history, 1):
                short = text if len(text) <= 60 else text[:57] + "…"
                self.recent_menu.add(rumps.MenuItem(f"{i}. {short}", callback=self._copy_cb(text)))
            self.recent_menu.add(rumps.MenuItem("— click an item to copy it —"))


def main() -> None:
    core.log("Starting Scribe Dictate menu-bar app…")
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    try:
        engine = core.Engine()
    except SystemExit as e:
        rumps.alert(title="Scribe Dictate", message=str(e))
        core.log(str(e))
        sys.exit(78)  # EX_CONFIG: tells the launcher not to auto-restart
    ScribeMenuBar(engine).run()


if __name__ == "__main__":
    main()
