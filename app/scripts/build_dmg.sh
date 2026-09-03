#!/bin/bash
# PreCut — build a distributable .zip of the .app bundle.
#
# Run BY THE DEVELOPER on a Mac to produce an arm64 (Apple Silicon)
# zipped .app that strangers can double-click on macOS 12+.
#
# End-user flow:
#   1. Download PreCut-0.3.0-arm64.zip
#   2. Double-click the zip → Finder extracts PreCut.app
#   3. Drag PreCut.app to /Applications
#   4. Right-click PreCut → Open → click "Open" on the Gatekeeper
#      dialog (one-time, because we're ad-hoc signed, not notarized)
#   5. First-time setup screen walks them through ffmpeg / Python deps
#
# This replaces the earlier .dmg approach because macOS 15 (Sequoia)
# removed the right-click-Open bypass for .dmg files specifically —
# but .app bundles still have it. Zipping preserves that UX path.
#
# What the script does:
#   1. Sanity-check the host (macOS + Xcode CLT + Rust + Node + Python3)
#   2. Build icon.icns from src-tauri/icons/icon.png via iconutil
#   3. Build the Tauri .app for aarch64-apple-darwin
#   4. Ad-hoc codesign the .app — REQUIRED on Apple Silicon, the app
#      will not launch at all without it
#   5. Archive the .app with `ditto` (not zip!) so the code signature
#      and macOS metadata survive the round trip
#   6. Verify signature and print the output path
#
# Usage:
#   ./scripts/build_dmg.sh           # full build (yes the name still has "dmg"
#                                    # — renamed to build_release.sh would
#                                    # invalidate existing muscle memory/docs,
#                                    # keeping the filename for now)
#   ./scripts/build_dmg.sh --skip-build    # reuse existing .app, just re-zip
#   ./scripts/build_dmg.sh --clean   # nuke target/ first
#
# Exit codes:
#   0 success, non-zero on any step failure.

set -euo pipefail

# ---- Paths ------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="PreCut"

# Read version from tauri.conf.json (single source of truth). We inline
# a tiny Python one-liner — cheaper than adding a jq dependency.
VERSION=$(
    /usr/bin/python3 -c "
import json, sys
with open('$APP_ROOT/src-tauri/tauri.conf.json') as f:
    print(json.load(f)['version'])
" 2>/dev/null || echo "0.0.0"
)
RELEASE_NAME="${APP_NAME}-${VERSION}-arm64"

TARGET_DIR="$APP_ROOT/src-tauri/target"
BUNDLE_DIR="$TARGET_DIR/aarch64-apple-darwin/release/bundle"
DIST_DIR="$APP_ROOT/dist-release"   # final .zip lands here

# ---- Flags ------------------------------------------------------------------

SKIP_BUILD=0
CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        --clean) CLEAN=1 ;;
        -h|--help)
            head -45 "$0" | sed -n '2,$p' | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

# ---- Colors -----------------------------------------------------------------

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${BLUE}>${NC} $*"; }
ok()    { echo -e "${GREEN}OK${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC}  $*"; }
fail()  { echo -e "${RED}X${NC} $*" >&2; }
heading() { echo; echo -e "${BOLD}$*${NC}"; echo "----------------------------------------"; }

# ---- 1. Host sanity ---------------------------------------------------------

heading "1/6  Host sanity"

if [[ "$(uname)" != "Darwin" ]]; then
    fail "build_dmg.sh only runs on macOS."
    exit 1
fi
ok "macOS $(sw_vers -productVersion)"

if [[ "$(uname -m)" != "arm64" ]]; then
    warn "Host is $(uname -m), not arm64."
    warn "Cross-compiling to aarch64-apple-darwin via rustup target."
    warn "Result is the same, but the build runs slower on Rosetta."
fi

# xcode-select is required for ad-hoc signing (codesign + iconutil live there)
if ! xcode-select -p >/dev/null 2>&1; then
    fail "Xcode Command Line Tools missing. Install with: xcode-select --install"
    exit 1
fi
ok "Xcode CLT at $(xcode-select -p)"

# Rust + target
if ! command -v cargo >/dev/null 2>&1; then
    fail "Rust/cargo not found. Install from https://rustup.rs/"
    exit 1
fi
ok "Rust: $(rustc --version)"

info "Ensuring aarch64-apple-darwin target is installed..."
rustup target add aarch64-apple-darwin >/dev/null 2>&1 || true
ok "aarch64-apple-darwin target ready"

# Node
if ! command -v node >/dev/null 2>&1; then
    fail "Node.js not found. brew install node (needs 18+)"
    exit 1
fi
NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
if [[ "$NODE_MAJOR" -lt 18 ]]; then
    fail "Node $NODE_MAJOR is too old (need 18+)."
    exit 1
fi
ok "Node $(node -v)"

# Python 3 for iconutil wrapper — need Pillow available
PYTHON3=/usr/bin/python3
if [[ ! -x "$PYTHON3" ]]; then
    PYTHON3=$(command -v python3 || echo "")
fi
if [[ -z "$PYTHON3" ]]; then
    fail "No python3 found. Install via Homebrew or Xcode CLT."
    exit 1
fi
ok "Python: $PYTHON3 ($($PYTHON3 --version 2>&1))"

if ! $PYTHON3 -c "import PIL" 2>/dev/null; then
    info "Installing Pillow (needed by icon builder)..."
    $PYTHON3 -m pip install --quiet --user --break-system-packages Pillow \
        || $PYTHON3 -m pip install --quiet --user Pillow \
        || { fail "Couldn't install Pillow"; exit 1; }
fi
ok "Pillow available"

# reportlab — needed for the install-guide PDF. Drop 4.44 change:
# the release zip includes a "Read me first - Install.pdf" alongside
# the .app so users see the macOS Sequoia "Open Anyway" instructions
# before they hit the security dialog.
if ! $PYTHON3 -c "import reportlab" 2>/dev/null; then
    info "Installing reportlab (needed by install-guide PDF builder)..."
    $PYTHON3 -m pip install --quiet --user --break-system-packages reportlab \
        || $PYTHON3 -m pip install --quiet --user reportlab \
        || { fail "Couldn't install reportlab"; exit 1; }
fi
ok "reportlab available"

# ---- 2. Clean (optional) ----------------------------------------------------

if [[ "$CLEAN" == "1" ]]; then
    heading "  Clean"
    rm -rf "$TARGET_DIR" "$DIST_DIR" "$APP_ROOT/dist"
    ok "Removed target/, dist/, dist-release/"
fi

mkdir -p "$DIST_DIR"

# ---- 3. npm install ---------------------------------------------------------

heading "2/6  npm install"

cd "$APP_ROOT"
if [[ ! -d node_modules ]] || [[ package.json -nt node_modules ]]; then
    info "Running npm install..."
    npm install
else
    ok "node_modules up to date"
fi

# ---- 4. Icons ---------------------------------------------------------------

heading "3/6  Icons"

ICONS_DIR="$APP_ROOT/src-tauri/icons"

# If icon.png is missing, fall back to the placeholder generator.
# Otherwise, preserve the designed icon.png and just build a fresh .icns
# from it via iconutil.
if [[ ! -f "$ICONS_DIR/icon.png" ]]; then
    warn "icon.png missing — falling back to placeholder generator."
    $PYTHON3 "$APP_ROOT/scripts/make_placeholder_icons.py" "$ICONS_DIR" || {
        fail "Icon generation failed"
        exit 1
    }
fi

info "Building icon.icns from icon.png via iconutil..."
$PYTHON3 "$APP_ROOT/scripts/make_icns_from_icon.py" "$ICONS_DIR" || {
    fail "Couldn't build icon.icns"
    exit 1
}
ok "Icons ready at $ICONS_DIR"

# ---- 5. Build the .app ------------------------------------------------------

heading "4/6  Tauri build (arm64)"

# Explicit --target flag gets us a pure arm64 binary regardless of
# whether the dev machine is Intel or Apple Silicon. We only bundle
# the .app — no DMG generation anymore.
if [[ "$SKIP_BUILD" == "1" ]]; then
    warn "Skipping tauri build (--skip-build)"
else
    info "Running: npx tauri build --bundles app --target aarch64-apple-darwin"
    info "(First build can take 10-15 min; incremental builds are ~1 min)"
    cd "$APP_ROOT"
    npx tauri build --bundles app --target aarch64-apple-darwin
fi

# Locate the built .app — Tauri's path conventions have shifted across
# versions, so we look in the most-likely spots.
BUILT_APP=""
for candidate in \
    "$BUNDLE_DIR/macos/$APP_NAME.app" \
    "$BUNDLE_DIR/macos/broll-buddy-app.app" \
    "$BUNDLE_DIR/app/$APP_NAME.app" \
    "$BUNDLE_DIR/app/broll-buddy-app.app" ; do
    if [[ -d "$candidate" ]]; then
        BUILT_APP="$candidate"
        break
    fi
done

# Last resort — broad search
if [[ -z "$BUILT_APP" ]]; then
    BUILT_APP=$(find "$TARGET_DIR/aarch64-apple-darwin" -type d \
        -name "*.app" -not -path "*/deps/*" 2>/dev/null | head -1)
fi

if [[ -z "$BUILT_APP" ]] || [[ ! -d "$BUILT_APP" ]]; then
    fail "Tauri build succeeded but no .app bundle found."
    fail "Looked in: $BUNDLE_DIR"
    exit 1
fi
ok "Built: $BUILT_APP"

# Rename broll-buddy-app.app -> PreCut.app so Finder/Dock show the
# user-facing name.
APP_BASENAME=$(basename "$BUILT_APP")
if [[ "$APP_BASENAME" != "$APP_NAME.app" ]]; then
    info "Renaming $APP_BASENAME -> $APP_NAME.app"
    NEW_PATH="$(dirname "$BUILT_APP")/$APP_NAME.app"
    rm -rf "$NEW_PATH"
    cp -R "$BUILT_APP" "$NEW_PATH"
    BUILT_APP="$NEW_PATH"
    INFO_PLIST="$BUILT_APP/Contents/Info.plist"
    if [[ -f "$INFO_PLIST" ]]; then
        /usr/libexec/PlistBuddy -c "Set :CFBundleName $APP_NAME" "$INFO_PLIST" 2>/dev/null || true
        /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $APP_NAME" "$INFO_PLIST" 2>/dev/null \
            || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $APP_NAME" "$INFO_PLIST" 2>/dev/null || true
    fi
fi

# ---- 6. Ad-hoc codesign -----------------------------------------------------

heading "5/6  Ad-hoc codesign"

# On Apple Silicon, unsigned binaries fail with SIGKILL at launch —
# not even a Gatekeeper dialog, just "killed: 9". An ad-hoc signature
# (identity "-") satisfies the kernel without a paid Developer ID.
#
# --deep so nested frameworks / the python_backend resource tree are
# also signed. --force overrides any existing signature. --timestamp=none
# because ad-hoc signatures can't be timestamped.
info "Signing $BUILT_APP with ad-hoc identity..."
codesign --force --deep --sign - \
    --timestamp=none \
    --options=runtime \
    "$BUILT_APP" 2>&1 | sed 's/^/  /' || {
    fail "codesign failed"
    exit 1
}

if codesign --verify "$BUILT_APP" 2>&1; then
    ok "Ad-hoc signature valid"
else
    warn "codesign --verify reported issues - app may still run, check output"
fi

SIG_INFO=$(codesign -dvv "$BUILT_APP" 2>&1 | grep -E "Identifier|Signature|Authority" | head -5)
echo -e "${DIM}${SIG_INFO}${NC}"

# ---- 7. Archive the .app ----------------------------------------------------

heading "6/6  Archive"

ZIP_PATH="$DIST_DIR/${RELEASE_NAME}.zip"
rm -f "$ZIP_PATH"

# Drop 4.44 change: the release zip now contains a folder with BOTH
# the .app and a PDF install guide. On macOS Sequoia, unsigned apps
# hit a Gatekeeper dialog with no bypass button; without the guide,
# users assume the app is broken. The PDF walks them through the
# "System Settings -> Open Anyway" flow with annotated screenshots.
#
# Staging layout:
#   PreCut-0.3.0-arm64/
#     PreCut.app/
#     Read Me First - Install Guide.pdf
#
# When a user double-clicks the zip, Finder extracts the top-level
# folder. They see the PDF right next to the app, and naturally read
# it before trying to run anything.

STAGING_PARENT=$(mktemp -d -t precut-release-staging)
trap 'rm -rf "$STAGING_PARENT"' EXIT
STAGING_DIR="$STAGING_PARENT/$RELEASE_NAME"
mkdir -p "$STAGING_DIR"

info "Staging release folder: $STAGING_DIR"
cp -R "$BUILT_APP" "$STAGING_DIR/"

# Generate the install-guide PDF directly into the staging folder.
# The filename starts with "Read Me First" so it sorts above the app
# in most locales (Finder sorts alphabetically by default).
PDF_PATH="$STAGING_DIR/Read Me First - Install Guide.pdf"
info "Generating install guide PDF..."
$PYTHON3 "$APP_ROOT/scripts/make_install_pdf.py" "$PDF_PATH" "$VERSION" || {
    fail "install-guide PDF generation failed"
    exit 1
}
ok "PDF: $PDF_PATH ($(du -h "$PDF_PATH" | cut -f1))"

# CRITICAL: use `ditto`, not `zip`.
#
# The regular `zip` command does not preserve macOS resource forks,
# extended attributes, or (most importantly) the code signature layout
# the way macOS expects. Apps zipped with `zip` often fail to launch
# after extraction with cryptic "damaged" errors from Gatekeeper, even
# when the pre-zipped .app ran fine.
#
# `ditto` with --sequesterRsrc stores resource-fork metadata in the
# AppleDouble __MACOSX format that round-trips cleanly through
# Finder/unzip. --keepParent preserves the outer folder (our staged
# release directory), so users extract a folder containing both the
# .app and the PDF — not a spray of loose files.
info "Archiving $STAGING_DIR -> $ZIP_PATH via ditto..."
ditto -c -k --sequesterRsrc --keepParent "$STAGING_DIR" "$ZIP_PATH"

if [[ ! -f "$ZIP_PATH" ]]; then
    fail "ditto produced no output. This shouldn't happen."
    exit 1
fi

ZIP_SIZE=$(du -h "$ZIP_PATH" | cut -f1)
ok "Created: $ZIP_PATH ($ZIP_SIZE)"

# Smoke-test the archive by extracting to /tmp and verifying:
#   1. The folder structure is what we expect (.app + .pdf)
#   2. The .app's code signature survived the round trip
info "Verifying the archive..."
VERIFY_DIR=$(mktemp -d -t precut-verify)
# Re-use the existing trap but ensure both dirs get cleaned up
trap 'rm -rf "$STAGING_PARENT" "$VERIFY_DIR"' EXIT

ditto -x -k "$ZIP_PATH" "$VERIFY_DIR"
EXTRACTED_FOLDER="$VERIFY_DIR/$RELEASE_NAME"
EXTRACTED_APP="$EXTRACTED_FOLDER/$APP_NAME.app"
EXTRACTED_PDF="$EXTRACTED_FOLDER/Read Me First - Install Guide.pdf"

if [[ ! -d "$EXTRACTED_FOLDER" ]]; then
    fail "Extracted archive doesn't contain $RELEASE_NAME/ at the top level."
    fail "Contents:"
    ls -la "$VERIFY_DIR" >&2
    exit 1
fi
if [[ ! -d "$EXTRACTED_APP" ]]; then
    fail "Extracted archive is missing $APP_NAME.app."
    exit 1
fi
if [[ ! -f "$EXTRACTED_PDF" ]]; then
    fail "Extracted archive is missing the install guide PDF."
    exit 1
fi
ok "Archive contains $APP_NAME.app and the install guide PDF"

if codesign --verify "$EXTRACTED_APP" 2>&1; then
    ok "Extracted app's signature is valid - archive round-trips cleanly"
else
    warn "Extracted app's signature is broken! The archive corrupted the bundle."
    warn "This is unusual for ditto - report the codesign output above."
    exit 1
fi

# ---- Summary ----------------------------------------------------------------

echo
echo -e "${BOLD}========================================${NC}"
echo -e "${GREEN}${BOLD}  Release ready${NC}"
echo -e "${BOLD}========================================${NC}"
echo
echo -e "  ${BOLD}$ZIP_PATH${NC}"
echo -e "  ${DIM}$ZIP_SIZE - ad-hoc signed - arm64${NC}"
echo
echo -e "  End user flow:"
echo -e "    1. ${DIM}Download and double-click the zip${NC}"
echo -e "    2. ${DIM}Finder extracts folder containing PreCut.app + install PDF${NC}"
echo -e "    3. ${DIM}Read the PDF, follow its 6 steps${NC}"
echo -e "    4. ${DIM}First-time setup screen installs ffmpeg + Python deps${NC}"
echo
echo -e "  To open the dist folder now:"
echo -e "    ${DIM}open '$DIST_DIR'${NC}"
echo
