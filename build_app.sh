#!/usr/bin/env bash
# Builds "Scribe Dictate.app" into ~/Applications.
#
# The app is a tiny native launcher that runs menubar.py from THIS folder,
# so editing the Python code never requires a rebuild — just use
# "Restart" in the menu.
#
# Usage:
#   ./build_app.sh            build (if needed) + launch
#   ./build_app.sh --login    …and add it to Login Items (start at login)
#   ./build_app.sh --force    rebuild even if nothing changed (resets permissions)
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Scribe Dictate"
BUNDLE_ID="${SCRIBE_BUNDLE_ID:-gr.adgurus.scribe-dictate}"  # override: SCRIBE_BUNDLE_ID=com.you.scribe ./build_app.sh
PROJECT_DIR="$(pwd -P)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
APP="$HOME/Applications/$APP_NAME.app"

FORCE=0
LOGIN=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --login) LOGIN=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

step() { printf '\033[1m▸ %s\033[0m\n' "$*"; }

# ── 1. Prerequisites & Python deps ─────────────────────────────────────────
command -v clang >/dev/null || {
  echo "✖ clang not found. Install Xcode Command Line Tools:  xcode-select --install"; exit 1; }

if [ ! -x "$PYTHON_BIN" ]; then
  step "Creating .venv"
  python3 -m venv .venv
fi
step "Installing Python dependencies"
"$PYTHON_BIN" -m pip install -q --upgrade pip
"$PYTHON_BIN" -m pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠ Created .env — add your ELEVENLABS_API_KEY, then run this script again."
  exit 1
fi
grep -Eq '^ELEVENLABS_API_KEY=.+' .env || { echo "✖ ELEVENLABS_API_KEY is empty in .env"; exit 1; }

# ── 2. Stop running instances (avoid double pastes) ───────────────────────
pkill -x ScribeDictate 2>/dev/null || true
pkill -f "$PROJECT_DIR/menubar.py" 2>/dev/null || true
if pkill -f "dictate.py" 2>/dev/null; then echo "  (stopped the terminal version)"; fi
sleep 0.5

# ── 3. Build the bundle (only when the launcher actually changed) ─────────
read -r -d '' INFO_PLIST <<PLIST || true
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>ScribeDictate</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Scribe Dictate records your voice while you hold the hotkey and sends it to ElevenLabs for transcription.</string>
</dict>
</plist>
PLIST

HASH="$( { cat launcher/launcher.c; echo "$PROJECT_DIR|$PYTHON_BIN"; echo "$INFO_PLIST"; } | shasum -a 256 | cut -d' ' -f1)"
STAMP="$APP/Contents/Resources/build.sha256"

if [ "$FORCE" = 0 ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$HASH" ]; then
  step "App is up to date — no rebuild needed (permissions kept)"
else
  REBUILD=0
  [ -d "$APP" ] && REBUILD=1
  step "Building $APP"
  rm -rf "$APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  clang -O2 -Wall -mmacosx-version-min=11.0 \
    -DPROJECT_DIR="\"$PROJECT_DIR\"" \
    -DPYTHON_BIN="\"$PYTHON_BIN\"" \
    -o "$APP/Contents/MacOS/ScribeDictate" launcher/launcher.c
  printf '%s\n' "$INFO_PLIST" > "$APP/Contents/Info.plist"
  echo "$HASH" > "$STAMP"
  codesign --force --sign - --identifier "$BUNDLE_ID" "$APP"

  if [ "$REBUILD" = 1 ]; then
    # A rebuilt binary has a new signature, so old permission entries go stale.
    step "Resetting old permission entries (you'll be asked again once)"
    tccutil reset All "$BUNDLE_ID" >/dev/null 2>&1 || true
  fi
fi

# ── 4. Optional: start at login ────────────────────────────────────────────
if [ "$LOGIN" = 1 ]; then
  if osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null \
      | grep -q "$APP_NAME"; then
    step "Already in Login Items"
  else
    step "Adding to Login Items"
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:true}" >/dev/null
  fi
fi

# ── 5. Launch ──────────────────────────────────────────────────────────────
step "Launching $APP_NAME"
open "$APP"

cat <<EOF

✔ Done. Look for 🎙 in the menu bar.

First launch only — approve these for "Scribe Dictate"
(System Settings → Privacy & Security, or the app's Permissions menu):
  • Microphone        • Input Monitoring        • Accessibility
The hotkey starts working by itself once Input Monitoring is on.
If paste still doesn't happen after granting Accessibility, use "Restart" in the menu.

Log:  ~/Library/Logs/ScribeDictate.log
EOF
