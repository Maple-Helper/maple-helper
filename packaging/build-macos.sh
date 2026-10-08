#!/usr/bin/env bash
# Builds Maple Helper for macOS: PyInstaller .app -> self-test -> DMG [-> DMG round trip].
# The macOS counterpart of build.ps1; CI and local builds run this same script.
#
#   packaging/build-macos.sh                                 # uses data/kb
#   packaging/build-macos.sh --kb-dir tests/fixtures/kb      # build without a real knowledge base
#   packaging/build-macos.sh --require-kb --test-dmg         # what the release workflow runs
#
# The app is ad-hoc signed (Apple Silicon refuses to run unsigned code), not notarized:
# players confirm it once in System Settings > Privacy & Security. See docs/RELEASING.md.
set -euo pipefail

KB_DIR=data/kb
REQUIRE_KB=0
TEST_DMG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kb-dir) KB_DIR=$2; shift 2 ;;
    --require-kb) REQUIRE_KB=1; shift ;;
    --test-dmg) TEST_DMG=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
ROOT=$(pwd)
if [ -n "${PYTHON:-}" ]; then :; elif [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
VERSION=$("$PYTHON" -c "import maplehelper; print(maplehelper.__version__)")
APP="$ROOT/dist/Maple Helper.app"
APP_EXE="Contents/MacOS/Maple Helper"
RELEASE="$ROOT/dist/release"
# unversioned, like MapleHelper-Setup.exe: releases/latest/download/MapleHelper-macOS.dmg always works
DMG="$RELEASE/MapleHelper-macOS.dmg"
echo "== Maple Helper $VERSION for macOS $(uname -m) (python: $PYTHON)"

selftest() {
  local exe=$1 report code
  report=$(mktemp -t maplehelper-selftest)
  local args=(--selftest "$report")
  if [ "$REQUIRE_KB" = 1 ]; then args+=(--require-kb); fi
  # windowed app: the verdict is the exit code plus the report file
  set +e; "$exe" "${args[@]}" >/dev/null 2>&1; code=$?; set -e
  if [ -s "$report" ]; then cat "$report"; else echo "(no self-test report written)"; fi
  rm -f "$report"
  if [ "$code" -ne 0 ]; then echo "Self-test failed for $exe (exit code $code)" >&2; exit 1; fi
}

# ---------------------------------------------------------------- 1. freeze
if [ -f "$KB_DIR/index.json" ]; then
  MAPLEHELPER_KB=$(cd "$KB_DIR" && pwd)
elif [ "$REQUIRE_KB" = 1 ]; then
  echo "No knowledge base at $KB_DIR (index.json missing)" >&2; exit 1
else
  echo "WARNING: no knowledge base at $KB_DIR; building with an empty KB"
  MAPLEHELPER_KB=""
fi
export MAPLEHELPER_KB
rm -rf dist build
"$PYTHON" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/maplehelper.spec

# one consistent ad-hoc signature over the whole bundle (PyInstaller signs piece by piece)
signdiag() {
  echo "== codesign failed; bundle diagnostics" >&2
  echo "-- broken symlinks:" >&2; find "$APP" -type l ! -exec test -e {} \; -print >&2 || true
  echo "-- hidden files/dirs:" >&2; find "$APP" -name '.*' -print >&2 || true
  echo "-- rapidocr in the bundle:" >&2; find "$APP" -path '*rapidocr*' -maxdepth 6 -print >&2 || true
  codesign --force --deep --sign - -vvvv "$APP" >&2 || true
  codesign --verify --deep --strict -vvvv "$APP" >&2 || true
  exit 1
}
codesign --force --deep --sign - "$APP" || signdiag
codesign --verify --deep --strict "$APP" || signdiag

# ---------------------------------------------------------------- 2. smoke test the frozen app
selftest "$APP/$APP_EXE"

# ---------------------------------------------------------------- 3. disk image (drag to Applications)
mkdir -p "$RELEASE"
staging=$(mktemp -d)
ditto "$APP" "$staging/Maple Helper.app"
ln -s /Applications "$staging/Applications"
rm -f "$DMG"
# hdiutil now and then fails with "Resource busy" on CI runners; a retry clears it
for attempt in 1 2 3; do
  if hdiutil create -volname "Maple Helper" -srcfolder "$staging" -fs HFS+ -format UDZO -ov "$DMG"; then break; fi
  if [ "$attempt" = 3 ]; then echo "hdiutil create failed" >&2; exit 1; fi
  echo "hdiutil create failed, retrying ($attempt)"; sleep 5
done
rm -rf "$staging"

# ---------------------------------------------------------------- 4. DMG round trip
if [ "$TEST_DMG" = 1 ]; then
  mnt=$(mktemp -d)
  echo "== Mounting $DMG"
  hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$mnt" >/dev/null
  trap 'hdiutil detach "$mnt" -force >/dev/null 2>&1 || true' EXIT
  [ -L "$mnt/Applications" ] || { echo "DMG is missing the Applications link" >&2; exit 1; }
  codesign --verify --deep --strict "$mnt/Maple Helper.app"
  selftest "$mnt/Maple Helper.app/$APP_EXE"
  # the self-tested app can still be closing: "Resource busy" failed the v0.14.0 release after SELFTEST OK.
  # The image is read-only and already tested, so the last try forces it
  for attempt in 1 2 3 4; do
    if [ "$attempt" = 4 ]; then hdiutil detach "$mnt" -force >/dev/null && break; echo "hdiutil detach failed" >&2; exit 1; fi
    if hdiutil detach "$mnt" >/dev/null; then break; fi
    echo "hdiutil detach failed, retrying ($attempt)"; sleep 5
  done
  trap - EXIT
  echo "== DMG round trip OK"
fi

ls -lh "$RELEASE"
