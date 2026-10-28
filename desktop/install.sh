#!/bin/bash
# تثبيت تذكيراتي على الماك:
#   curl -fsSL https://abwoowd6-dotcom.github.io/tathkerati/desktop/install.sh | bash
set -e

BASE="https://abwoowd6-dotcom.github.io/tathkerati/desktop"
APP="$HOME/Library/Application Support/Tathkerati"
BUNDLE="$HOME/Applications/تذكيراتي.app"
PY_VERSION="3.12.10"

say() { printf "\n\033[1;34m▸ %s\033[0m\n" "$1"; }

# ---------- بايثون فيه Tk 8.6 (بايثون الماك الأصلي فيه Tk قديم ما يشتغل) ----------
find_python() {
  for p in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$p" ] && "$p" -c "import tkinter, sys; assert tkinter.TkVersion >= 8.6 and sys.version_info >= (3, 10)" 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

say "أدوّر على بايثون"
if ! PY=$(find_python); then
  say "ما لقيت بايثون مناسب، بنزّل بايثون $PY_VERSION من python.org (بيطلب كلمة مرور الماك)"
  PKG="/tmp/python-$PY_VERSION.pkg"
  curl -fL --progress-bar "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-macos11.pkg" -o "$PKG"
  sudo installer -pkg "$PKG" -target /
  rm -f "$PKG"
  PY=$(find_python) || { echo "❌ ما قدرت أثبّت بايثون"; exit 1; }
fi
echo "  $PY"

# ---------- ملفات البرنامج ----------
say "أنزّل البرنامج"
mkdir -p "$APP"
for f in reminders.pyw cloud.py icon.ico icon.png; do
  curl -fsSL "$BASE/$f?v=$(date +%s)" -o "$APP/$f"
done

say "أجهّز المكتبات"
[ -x "$APP/venv/bin/python" ] || "$PY" -m venv "$APP/venv"
"$APP/venv/bin/python" -m pip install -q --upgrade pip
"$APP/venv/bin/python" -m pip install -q "customtkinter==5.2.2" "pillow>=10" certifi

# ---------- التطبيق في مجلد Applications ----------
say "أسوي التطبيق"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cat > "$BUNDLE/Contents/MacOS/tathkerati" <<EOF
#!/bin/bash
exec "$APP/venv/bin/python" "$APP/reminders.pyw" "\$@"
EOF
chmod +x "$BUNDLE/Contents/MacOS/tathkerati"
cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>تذكيراتي</string>
  <key>CFBundleDisplayName</key><string>تذكيراتي</string>
  <key>CFBundleIdentifier</key><string>com.abwoo.tathkerati</string>
  <key>CFBundleExecutable</key><string>tathkerati</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

# أيقونة icns من الصورة
ICONSET="/tmp/tathkerati.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$APP/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$APP/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/icon.icns"
rm -rf "$ICONSET"
touch "$BUNDLE"

# ---------- يشتغل مع الماك ----------
say "أخليه يشتغل أول ما تفتح الماك"
PLIST="$HOME/Library/LaunchAgents/com.abwoo.tathkerati.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.abwoo.tathkerati</string>
  <key>ProgramArguments</key><array>
    <string>$APP/venv/bin/python</string><string>$APP/reminders.pyw</string><string>--tray</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
EOF

say "✓ خلصنا! أفتح لك تذكيراتي"
pkill -f "$APP/reminders.pyw" 2>/dev/null || true
open "$BUNDLE"
echo "  تلقاه بعدين في مجلد Applications حقك، أو ابحث عنه بـ Spotlight (⌘ + مسافة)"
