"""
Scribe Dictate — core engine (shared by the CLI and the menu-bar app).

hotkey → record mic → ElevenLabs Scribe → paste at cursor
"""
from __future__ import annotations

import collections
import io
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
from dotenv import load_dotenv

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

# --------------------------------------------------------------------------- #
# Config (.env lives next to this file)
# --------------------------------------------------------------------------- #
CONFIG_DIR = Path(os.environ.get("SCRIBE_CONFIG_DIR") or Path(__file__).resolve().parent)
ENV_FILE = CONFIG_DIR / ".env"
KEYTERMS_FILE = CONFIG_DIR / "keyterms.txt"
LOG_FILE = Path.home() / "Library" / "Logs" / "ScribeDictate.log"
load_dotenv(ENV_FILE)


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str) -> list[str]:
    return [p.strip() for p in env_str(name).split(",") if p.strip()]


API_KEY = env_str("ELEVENLABS_API_KEY")
MODEL_ID = env_str("MODEL_ID", "scribe_v3")
HOTKEY = env_str("HOTKEY", "right_option").lower()
MODE = env_str("MODE", "hold").lower()  # hold | toggle
LANGUAGE_CODE = env_str("LANGUAGE_CODE") or None
SECONDARY_LANGUAGE_CODES = env_list("SECONDARY_LANGUAGE_CODES")
NUMBERS_FORMAT = env_str("NUMBERS_FORMAT", "digits") or None  # digits | words
APPEND_SPACE = env_bool("APPEND_SPACE", True)
RESTORE_CLIPBOARD = env_bool("RESTORE_CLIPBOARD", True)
SOUNDS = env_bool("SOUNDS", True)
MIN_SECONDS = float(env_str("MIN_SECONDS", "0.4"))
MAX_SECONDS = float(env_str("MAX_SECONDS", "300"))
INPUT_DEVICE = env_str("INPUT_DEVICE") or None  # name substring or index

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
API_ROOT = "https://api.elevenlabs.io/"
SAMPLE_RATE = 16_000

# --------------------------------------------------------------------------- #
# Hotkeys (macOS virtual keycodes)
# --------------------------------------------------------------------------- #
# Modifier keys arrive as "flagsChanged"; the device-specific bit tells left/right apart.
MODIFIER_KEYS = {
    "right_option": (61, 0x40),
    "left_option": (58, 0x20),
    "right_command": (54, 0x10),
    "left_command": (55, 0x08),
    "right_control": (62, 0x2000),
    "left_control": (59, 0x01),
    "right_shift": (60, 0x04),
    "left_shift": (56, 0x02),
    "fn": (63, 0x800000),
}
REGULAR_KEYS = {
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}
KEY_ESCAPE = 53
KEY_V = 9  # physical V key — works with the Greek layout too

SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"
SOUND_ERROR = "/System/Library/Sounds/Basso.aiff"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def play(sound: str) -> None:
    if SOUNDS:
        subprocess.Popen(["/usr/bin/afplay", sound],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_keyterms() -> list[str]:
    """One term per line in keyterms.txt. Lines starting with # are ignored."""
    if not KEYTERMS_FILE.exists():
        return []
    terms, bad_chars = [], set("<>{}[]\\")
    for raw in KEYTERMS_FILE.read_text(encoding="utf-8").splitlines():
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        if len(term) > 50 or len(term.split()) > 5 or bad_chars & set(term):
            log(f"⚠ Skipping keyterm (max 50 chars / 5 words, no <>{{}}[]\\): {term!r}")
            continue
        terms.append(term)
    return terms[:1000]


def resolve_input_device(device: str | int | None) -> int | None:
    if device is None or isinstance(device, int):
        return device
    if device.isdigit():
        return int(device)
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and device.lower() in dev["name"].lower():
            return idx
    log(f"⚠ INPUT_DEVICE '{device}' not found — using system default mic")
    return None


def permissions(prompt: bool = False) -> dict[str, bool]:
    """Input Monitoring (hotkey) and Accessibility (paste). Optionally trigger the macOS prompts."""
    listen = post = True
    if hasattr(Quartz, "CGPreflightListenEventAccess"):
        listen = bool(Quartz.CGPreflightListenEventAccess())
        if not listen and prompt:
            Quartz.CGRequestListenEventAccess()
    if hasattr(Quartz, "CGPreflightPostEventAccess"):
        post = bool(Quartz.CGPreflightPostEventAccess())
        if not post and prompt:
            Quartz.CGRequestPostEventAccess()
    return {"input_monitoring": listen, "accessibility": post}


# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #
class Recorder:
    def __init__(self, device: str | int | None):
        self.device = device
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.started_at = 0.0
        self.max_frames = int(MAX_SECONDS * SAMPLE_RATE)
        self.n_frames = 0

    def start(self) -> None:
        try:
            self.stream = self._open_stream()
        except sd.PortAudioError as exc:
            # PortAudio caches Core Audio devices until it is reinitialized.
            # This recorder owns the only stream and _open_stream has cleaned it up.
            log(f"⚠ Microphone start failed ({exc}) — refreshing audio devices and retrying")
            if sd._initialized:
                sd._terminate()
            sd._initialize()
            self.stream = self._open_stream()
        self.started_at = time.monotonic()

    def _open_stream(self) -> sd.InputStream:
        self.frames, self.n_frames = [], 0
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            device=resolve_input_device(self.device), callback=self._callback,
        )
        try:
            stream.start()
        except sd.PortAudioError:
            stream.close()
            raise
        return stream

    def _callback(self, indata, frames, time_info, status) -> None:
        if self.n_frames < self.max_frames:
            self.frames.append(indata.copy())
            self.n_frames += frames

    def stop(self) -> tuple[bytes, float, bool]:
        """Returns (wav_bytes, duration_seconds, is_silent)."""
        duration = time.monotonic() - self.started_at
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.frames:
            return b"", duration, True
        audio = np.concatenate(self.frames)
        silent = int(np.abs(audio).max()) == 0  # all zeros = mic permission denied
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        return buf.getvalue(), duration, silent


# --------------------------------------------------------------------------- #
# ElevenLabs Scribe
# --------------------------------------------------------------------------- #
class Scribe:
    def __init__(self, keyterms: list[str]):
        self.session = requests.Session()
        self.session.headers["xi-api-key"] = API_KEY
        self.keyterms = keyterms
        self.last_used = 0.0
        self.language_code: str | None = LANGUAGE_CODE
        self.secondary: list[str] = list(SECONDARY_LANGUAGE_CODES)

    def warm_up(self) -> None:
        """Open the TLS connection while the user is still talking."""
        if time.monotonic() - self.last_used < 30:
            return

        def _go():
            try:
                self.session.get(API_ROOT, timeout=5)
                self.last_used = time.monotonic()
            except requests.RequestException:
                pass
        threading.Thread(target=_go, daemon=True).start()

    def transcribe(self, wav_bytes: bytes) -> str:
        data: list[tuple[str, str]] = [
            ("model_id", MODEL_ID),
            ("tag_audio_events", "false"),
            ("timestamps_granularity", "none"),
        ]
        if self.language_code:
            data.append(("language_code", self.language_code))
        if MODEL_ID.startswith("scribe_v3"):
            if self.language_code:
                data += [("secondary_language_codes", c) for c in self.secondary]
            if NUMBERS_FORMAT:
                data.append(("numbers_format", NUMBERS_FORMAT))
        data += [("keyterms", t) for t in self.keyterms]

        resp = self.session.post(
            API_URL,
            data=data,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            timeout=60,
        )
        self.last_used = time.monotonic()
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
        return (resp.json().get("text") or "").strip()


# --------------------------------------------------------------------------- #
# Clipboard + paste
# --------------------------------------------------------------------------- #
TRANSIENT_TYPE = "org.nspasteboard.TransientType"  # clipboard managers skip these


def _snapshot(pb) -> list[dict]:
    items = []
    for item in pb.pasteboardItems() or []:
        entry = {}
        for t in item.types():
            data = item.dataForType_(t)
            if data is not None:
                entry[t] = data
        if entry:
            items.append(entry)
    return items


def _restore(pb, items: list[dict]) -> None:
    pb.clearContents()
    if not items:
        return
    restored = []
    for entry in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in entry.items():
            it.setData_forType_(data, t)
        restored.append(it)
    restored[0].setString_forType_("", TRANSIENT_TYPE)
    pb.writeObjects_(restored)


def _press_cmd_v() -> None:
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for is_down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, KEY_V, is_down)
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def copy_to_clipboard(text: str) -> None:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def paste_text(text: str) -> bool:
    """Paste at the cursor. Returns False if Accessibility is missing (text is left on the clipboard)."""
    can_paste = permissions()["accessibility"]
    pb = NSPasteboard.generalPasteboard()
    saved = _snapshot(pb) if (RESTORE_CLIPBOARD and can_paste) else None

    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    if can_paste:
        pb.setString_forType_("", TRANSIENT_TYPE)
    else:
        return False  # leave it on the clipboard so the user can ⌘V manually
    our_change = pb.changeCount()

    time.sleep(0.03)
    _press_cmd_v()

    if saved is not None:
        time.sleep(0.4)  # give the target app time to read the clipboard
        if pb.changeCount() == our_change:  # don't clobber something the user copied meanwhile
            _restore(pb, saved)
    return True


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class Engine:
    def __init__(self):
        if not API_KEY:
            raise SystemExit(f"✖ ELEVENLABS_API_KEY is empty. Add it to {ENV_FILE}")
        if HOTKEY in MODIFIER_KEYS:
            self.hk_code, self.hk_flag = MODIFIER_KEYS[HOTKEY]
            self.hk_is_modifier = True
        elif HOTKEY in REGULAR_KEYS:
            self.hk_code, self.hk_flag = REGULAR_KEYS[HOTKEY], 0
            self.hk_is_modifier = False
        else:
            raise SystemExit(f"Unknown HOTKEY '{HOTKEY}'. Options: "
                             f"{', '.join(list(MODIFIER_KEYS) + list(REGULAR_KEYS))}")
        if MODE not in ("hold", "toggle"):
            raise SystemExit("MODE must be 'hold' or 'toggle'")

        self.keyterms = load_keyterms()
        self.scribe = Scribe(self.keyterms)
        self.recorder = Recorder(INPUT_DEVICE)

        # state (read by the UI)
        self.enabled = True
        self.recording = False
        self.pending = 0
        self.error_until = 0.0
        self.last_error = ""
        self.history: collections.deque[str] = collections.deque(maxlen=10)
        self.history_version = 0

        self.hotkey_down = False
        self.tap = None
        self._control: queue.Queue[str] = queue.Queue()
        self._jobs: queue.Queue[tuple[bytes, float]] = queue.Queue()
        threading.Thread(target=self._control_loop, daemon=True).start()
        threading.Thread(target=self._job_loop, daemon=True).start()

    # ---- public ----------------------------------------------------------- #
    @property
    def status(self) -> str:
        if not self.enabled:
            return "paused"
        if self.recording:
            return "recording"
        if self.pending > 0:
            return "transcribing"
        if time.monotonic() < self.error_until:
            return "error"
        return "idle"

    def set_language(self, code: str | None, secondary: list[str]) -> None:
        self.scribe.language_code = code
        self.scribe.secondary = list(secondary)
        log(f"Language → {code or 'auto-detect'}" + (f" (+{','.join(secondary)})" if code and secondary else ""))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled and self.recording:
            self._control.put("cancel_quiet")
        log("▶ Dictation resumed" if enabled else "⏸ Dictation paused")

    def describe(self) -> str:
        lang = self.scribe.language_code or "auto-detect"
        if self.scribe.language_code and self.scribe.secondary:
            lang += f" (+{','.join(self.scribe.secondary)})"
        return f"model={MODEL_ID} · lang={lang} · keyterms={len(self.keyterms)}"

    def install_tap(self, run_loop) -> bool:
        """Listen for the hotkey. Returns False if Input Monitoring isn't granted."""
        if self.tap is not None:
            return True
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._tap_callback,
            None,
        )
        if tap is None:
            return False
        self.tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(run_loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        return True

    # ---- event tap (main thread; must stay fast) -------------------------- #
    def _tap_callback(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)
            return event
        if not self.enabled:
            return event

        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)

        if etype == Quartz.kCGEventFlagsChanged:
            if self.hk_is_modifier and keycode == self.hk_code:
                self._on_hotkey(bool(Quartz.CGEventGetFlags(event) & self.hk_flag))

        elif etype == Quartz.kCGEventKeyDown:
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                return event
            if not self.hk_is_modifier and keycode == self.hk_code:
                self._on_hotkey(True)
            elif keycode == KEY_ESCAPE and self.recording:
                self._control.put("cancel")
            elif self.recording and MODE == "hold" and self.hk_is_modifier and self.hotkey_down:
                # e.g. Option+E for an accent: the user is typing a shortcut, not dictating
                self._control.put("cancel_quiet")

        elif etype == Quartz.kCGEventKeyUp:
            if not self.hk_is_modifier and keycode == self.hk_code:
                self._on_hotkey(False)

        return event

    def _on_hotkey(self, down: bool) -> None:
        if down == self.hotkey_down:
            return
        self.hotkey_down = down
        if MODE == "hold":
            self._control.put("start" if down else "stop")
        elif down:
            self._control.put("toggle")

    # ---- recording control (worker thread) -------------------------------- #
    def _flag_error(self, msg: str) -> None:
        self.last_error = msg
        self.error_until = time.monotonic() + 4
        log(f"✖ {msg}")
        play(SOUND_ERROR)

    def _control_loop(self) -> None:
        while True:
            cmd = self._control.get()
            if cmd == "toggle":
                cmd = "stop" if self.recording else "start"

            if cmd == "start" and not self.recording:
                try:
                    self.recorder.start()
                except Exception as e:  # noqa: BLE001
                    self._flag_error(f"Microphone error: {e}")
                    continue
                self.recording = True
                self.scribe.warm_up()
                play(SOUND_START)
                log("● Recording…")

            elif cmd == "stop" and self.recording:
                wav, dur, silent = self.recorder.stop()
                self.recording = False
                play(SOUND_STOP)
                if dur < MIN_SECONDS or not wav:
                    log(f"· Too short ({dur:.2f}s) — ignored")
                    continue
                if silent:
                    self._flag_error("Mic returned pure silence — check Microphone permission")
                    continue
                self.pending += 1
                self._jobs.put((wav, dur))

            elif cmd in ("cancel", "cancel_quiet") and self.recording:
                self.recorder.stop()
                self.recording = False
                if cmd == "cancel":
                    play(SOUND_ERROR)
                    log("✕ Cancelled")

    # ---- transcription + paste (worker thread, keeps order) --------------- #
    def _job_loop(self) -> None:
        while True:
            wav, dur = self._jobs.get()
            try:
                t0 = time.monotonic()
                try:
                    text = self.scribe.transcribe(wav)
                except Exception as e:  # noqa: BLE001
                    self._flag_error(f"Transcription failed: {e}")
                    continue
                api_s = time.monotonic() - t0
                if not text:
                    log(f"· Nothing recognised ({dur:.1f}s audio, {api_s:.2f}s)")
                    continue
                self.history.appendleft(text)
                self.history_version += 1
                if paste_text(text + (" " if APPEND_SPACE else "")):
                    log(f"✔ {dur:.1f}s audio → {api_s:.2f}s | {text}")
                else:
                    self._flag_error("Accessibility not granted — text copied, press ⌘V")
            finally:
                self.pending -= 1
