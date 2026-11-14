# 🎙 Scribe Dictate

**Push-to-talk dictation for macOS, powered by [ElevenLabs Scribe](https://elevenlabs.io/speech-to-text).**
Hold a key, speak, and release. Your words are typed into whatever app has focus: Slack, Gmail, Notion, VS Code, ChatGPT, anywhere.

A lightweight, self-hosted alternative to tools like Wispr Flow. You pay only for your own ElevenLabs API usage, a few cents per hour of speech.

- ⚡ **Hold-to-talk** on Right Option (configurable), or press-to-toggle
- 🌍 **90+ languages** with auto-detection and support for switching languages mid-sentence (e.g. Greek + English)
- 📋 **Pastes at your cursor** and restores your previous clipboard afterwards
- 🔒 Dictations are marked *transient*, so clipboard managers (Raycast, Paste, Maccy) don't log them
- 🧠 **Keyterms**: teach it names and jargon it should always spell correctly
- 🍎 Native **menu-bar app** that starts at login and shows recording status
- 🪶 ~600 lines of Python, no Electron, no background servers

> Not affiliated with ElevenLabs or Wispr.

---

## Requirements

| | |
|---|---|
| macOS | 11 Big Sur or later (Apple Silicon or Intel) |
| Python | 3.9+ (`python3 --version`) |
| Xcode Command Line Tools | `xcode-select --install` (provides `clang` and `git`) |
| ElevenLabs API key | [elevenlabs.io → Developers → API Keys](https://elevenlabs.io/app/settings/api-keys). The key needs the **Speech to Text** permission |

## Installation

```bash
# 1. Clone
git clone https://github.com/alefbetac/scribe.git
cd scribe

# 2. Configure
cp .env.example .env
open -t .env                 # paste your key into ELEVENLABS_API_KEY=

# 3. Build, install and start at login
./build_app.sh --login
```

This creates `~/Applications/Scribe Dictate.app`, adds it to your Login Items and launches it. Look for **🎙** in the menu bar.

### Grant permissions (first launch only)

macOS asks for three permissions for **Scribe Dictate**. You can also open each settings pane from the app's **Permissions** menu.

| Permission | Why it's needed |
|---|---|
| **Microphone** | To record while you hold the hotkey |
| **Input Monitoring** | To detect the hotkey in any app. The hotkey starts working within ~3 s of granting |
| **Accessibility** | To press ⌘V for you. Without it, the text is copied and you paste it yourself |

All three are under *System Settings → Privacy & Security*.

## Usage

| Action | How |
|---|---|
| Dictate | **Hold Right Option**, speak, release |
| Cancel a recording | `Esc` |
| Option shortcuts (e.g. ⌥E for accents) | Still work: pressing another key cancels the dictation |
| Re-copy a recent dictation | Menu → **Recent dictations** → click one |
| Switch language | Menu → **Language** |
| Pause | Menu → **Pause dictation** |

**Menu-bar icon:** 🎙 ready · 🔴 recording · ⏳ transcribing · ⚠️ needs attention (the menu says why) · ⏸ paused

You'll hear a *Tink* when recording starts, a *Pop* when it stops, and a *Basso* on errors (you can turn these off).

## Configuration

All settings live in `.env` (see [`.env.example`](.env.example)). After editing, click **Restart** in the menu.

| Setting | Default | Options |
|---|---|---|
| `ELEVENLABS_API_KEY` | — | Your API key (**required**) |
| `MODEL_ID` | `scribe_v3` | `scribe_v3`, `scribe_v2` |
| `HOTKEY` | `right_option` | `right_option`, `right_command`, `right_control`, `right_shift`, the `left_*` versions, `fn`, `f13`–`f20` |
| `MODE` | `hold` | `hold` (push-to-talk) · `toggle` (press to start, press again to stop) |
| `LANGUAGE_CODE` | *(empty = auto-detect)* | ISO code, e.g. `en`, `el`, `de` |
| `SECONDARY_LANGUAGE_CODES` | *(empty)* | Extra languages you mix in, e.g. `en` (only used when `LANGUAGE_CODE` is set) |
| `NUMBERS_FORMAT` | `digits` | `digits` ("42") · `words` ("forty-two") |
| `APPEND_SPACE` | `true` | Adds a space after each dictation so consecutive ones read naturally |
| `RESTORE_CLIPBOARD` | `true` | Puts your previous clipboard back after pasting |
| `SOUNDS` | `true` | Audio feedback |
| `MIN_SECONDS` / `MAX_SECONDS` | `0.4` / `300` | Ignore accidental taps / cap recording length |
| `INPUT_DEVICE` | *(system default)* | Microphone name substring (e.g. `MacBook`, `Shure`) or index |

**Using `fn` as the hotkey:** first set *System Settings → Keyboard → "Press 🌐 key to" → Do Nothing*.

### Keyterms (optional)

To make sure names and jargon are spelled correctly:

```bash
cp keyterms.example.txt keyterms.txt   # or: menu → Edit keyterms…
```

Add one term per line. `keyterms.txt` is git-ignored, so client and product names stay private. Keyterms add roughly 20% to the API cost.

## Cost

You're billed by ElevenLabs per hour of audio. At the time of writing (Nov 2026), Scribe v3 costs about **$0.20 per hour**. Dictating for 1 hour every workday comes to roughly **$4–5 per month**. Check [current pricing](https://elevenlabs.io/pricing/api).

## Privacy

- Audio is recorded **only while you hold the hotkey**, and the macOS mic indicator shows when that happens.
- Recordings are sent to the ElevenLabs API for transcription and are not stored locally. ElevenLabs' [data handling terms](https://elevenlabs.io/privacy) apply. Enterprise accounts can use Zero Retention Mode.
- Your API key stays in `.env` on your machine. It is git-ignored and never logged.

## How it works

```
Scribe Dictate.app            tiny native launcher (C, ad-hoc signed)
  └─ .venv/bin/python menubar.py      menu-bar UI (rumps)
       └─ scribe_core.py
            hotkey (CGEventTap) → mic (sounddevice, 16 kHz WAV)
            → POST api.elevenlabs.io/v1/speech-to-text
            → clipboard + ⌘V (CGEvent) → clipboard restored
```

- The `.app` is only a launcher. The Python code runs from your clone, so **code changes don't need a rebuild**: click **Restart** in the menu.
- Because the app is the "responsible process", macOS attributes the permissions to *Scribe Dictate* instead of Python or Terminal.
- The launcher restarts the app if it crashes (up to 5 times per minute) and writes output to `~/Library/Logs/ScribeDictate.log`.

### Project structure

```
├── build_app.sh          builds & installs Scribe Dictate.app
├── launcher/launcher.c   native app launcher
├── menubar.py            menu-bar app
├── scribe_core.py        engine: hotkey, recording, API, paste
├── dictate.py            terminal version (debugging)
├── run.sh                runs the terminal version
├── .env.example          settings template
└── keyterms.example.txt  keyterms template
```

## Terminal mode (for debugging)

```bash
./run.sh
```

This runs the same engine in the foreground and logs to the terminal. In this mode, the permissions belong to your terminal app instead. Don't run it alongside the menu-bar app, or every dictation will be pasted twice. `build_app.sh` stops it for you.

## Troubleshooting

| Problem | Fix |
|---|---|
| Hotkey does nothing | Grant **Input Monitoring** to Scribe Dictate |
| Log shows ✔ but nothing is pasted | Grant **Accessibility**, then click **Restart** |
| "Mic returned pure silence" | Grant **Microphone**, or check `INPUT_DEVICE` |
| `HTTP 401` in the log | Wrong API key, or the key lacks the Speech to Text permission |
| Permissions look enabled but don't work | Remove Scribe Dictate from the permission lists, run `./build_app.sh --force`, and grant again |
| Doesn't paste into password fields | Expected: macOS blocks simulated typing there (Secure Input) |
| First syllable cut off | Start speaking after the *Tink* sound |

Logs: `~/Library/Logs/ScribeDictate.log`, or menu → **Open log**.

### When to re-run `build_app.sh`

Only after moving the folder or recreating `.venv`. The script skips the rebuild if nothing changed. A real rebuild changes the app's signature, so macOS asks for the three permissions again.

## Uninstall

```bash
pkill -x ScribeDictate
rm -rf ~/Applications/"Scribe Dictate.app"
tccutil reset All gr.adgurus.scribe-dictate
```

Then remove it from *System Settings → General → Login Items*.

> If you built with a custom `SCRIBE_BUNDLE_ID`, use that ID in the `tccutil` command.

## Roadmap ideas

- [ ] Streaming with Scribe v3 Live (text ready the moment you release the key)
- [ ] Optional LLM cleanup pass (filler words, punctuation, tone per app)
- [ ] Remember the language choice across restarts

## License

[MIT](LICENSE)
