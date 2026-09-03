#!/bin/bash
# PreCut — one-shot Mac setup + build script.
#
# What this does:
#   1. Check macOS + Homebrew + Xcode Command Line Tools
#   2. Install Rust (rustup) if missing
#   3. Install Node.js (via Homebrew) if missing
#   4. Install npm dependencies
#   5. Build the .app
#   6. Copy to ~/Applications/PreCut.app
#
# Total runtime on first run: ~10–15 minutes (Rust compiles a lot).
# Subsequent runs (after source changes): ~1–2 minutes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="PreCut"
# Legacy name the build used to use — we clean these up if present so old
# B-Roll Buddy.app doesn't linger next to the new PreCut.app.
LEGACY_APP_NAMES=("B-Roll Buddy")
INSTALL_DIR="$HOME/Applications"

# ---- Colors -----------------------------------------------------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BLUE='\033[0;34m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
fail()  { echo -e "${RED}✗${NC} $*" >&2; }
heading() { echo; echo -e "${BOLD}$*${NC}"; echo "────────────────────────────────────────"; }

# ---- Platform check ---------------------------------------------------------

heading "1/7  Platform checks"

if [[ "$(uname)" != "Darwin" ]]; then
    fail "This builder only runs on macOS."
    exit 1
fi

MACOS_VER=$(sw_vers -productVersion)
ok "macOS $MACOS_VER detected"

# ---- Xcode Command Line Tools -----------------------------------------------

if ! xcode-select -p >/dev/null 2>&1; then
    warn "Xcode Command Line Tools not installed."
    echo "   This will trigger a GUI installer — approve and wait for it to finish."
    xcode-select --install || true
    fail "Re-run this script after the installer completes."
    exit 1
fi
ok "Xcode Command Line Tools present"

# ---- Homebrew ---------------------------------------------------------------

if ! command -v brew >/dev/null 2>&1; then
    fail "Homebrew is not installed."
    echo "   Install with:"
    echo '     /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo "   Then restart your terminal and run this script again."
    exit 1
fi
ok "Homebrew present: $(brew --version | head -1)"

# ---- FFmpeg (required at runtime, not build) --------------------------------

if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "FFmpeg not installed — the app will build but won't work until you install it."
    echo "   Install with: brew install ffmpeg"
else
    ok "FFmpeg present"
fi

# ---- Rust -------------------------------------------------------------------

heading "2/7  Rust toolchain"

if ! command -v rustc >/dev/null 2>&1; then
    info "Installing Rust via rustup (takes ~2 min)…"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
    # Source cargo env for this session
    source "$HOME/.cargo/env"
else
    ok "Rust present: $(rustc --version)"
    # Make sure we're on stable
    rustup default stable >/dev/null 2>&1 || true
fi

# Ensure both architectures are available on Apple Silicon — Tauri can build universal
if [[ "$(uname -m)" == "arm64" ]]; then
    rustup target add x86_64-apple-darwin aarch64-apple-darwin >/dev/null 2>&1 || true
fi

# ---- Node.js ----------------------------------------------------------------

heading "3/7  Node.js"

if ! command -v node >/dev/null 2>&1; then
    info "Installing Node.js via Homebrew…"
    brew install node
else
    NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
    if [[ "$NODE_MAJOR" -lt 18 ]]; then
        warn "Node $NODE_MAJOR is too old (need 18+). Upgrading via Homebrew…"
        brew upgrade node
    else
        ok "Node present: $(node -v)"
    fi
fi

# ---- Install Python backend dependencies -----------------------------------

heading "4/7  Python backend dependencies"

REQ_FILE="$APP_ROOT/python_backend/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
    info "Installing Python packages (this may take 5-10 min on first install — torch is large)…"
    # --break-system-packages needed on macOS 14+ (externally-managed)
    # --user keeps everything out of the system site-packages
    # Pin Python to 3 explicitly in case python3 resolves to something weird
    pip3 install --user --break-system-packages -r "$REQ_FILE" 2>&1 | \
        grep -E "Requirement already satisfied|Successfully installed|ERROR" | \
        head -20 || true
    ok "Python backend dependencies installed"
else
    warn "requirements.txt not found, skipping Python deps"
fi

# ---- Install npm dependencies -----------------------------------------------

heading "5/7  Frontend dependencies"

cd "$APP_ROOT"
if [[ ! -d node_modules ]] || [[ package.json -nt node_modules ]]; then
    info "Running npm install…"
    npm install
else
    ok "npm dependencies up to date"
fi

# ---- Build placeholder icons if missing -------------------------------------

# Icons ship pre-baked in src-tauri/icons/, but we'll regenerate if they're
# missing (e.g. someone deleted them). This step is NOT required for a
# successful build — if it fails we continue.

ICONS_DIR="$APP_ROOT/src-tauri/icons"
if [[ ! -f "$ICONS_DIR/32x32.png" ]] || [[ ! -f "$ICONS_DIR/128x128.png" ]]; then
    info "Icons missing — regenerating…"

    # PIL (Pillow) is needed for the icon generator. Install if missing.
    if ! python3 -c "import PIL" 2>/dev/null; then
        info "Installing Pillow for icon generation…"
        # --break-system-packages is needed on macOS 14+ which treats system
        # Python as externally-managed. --user keeps it out of system site-packages.
        pip3 install --quiet --user --break-system-packages Pillow 2>/dev/null \
            || pip3 install --quiet --user Pillow 2>/dev/null \
            || true
    fi

    if python3 "$APP_ROOT/scripts/make_placeholder_icons.py" "$ICONS_DIR" 2>/dev/null; then
        ok "Placeholder icons generated"
    else
        warn "Couldn't generate icons — continuing anyway (Tauri may warn)"
    fi
else
    ok "Icons present"
fi

# Ensure .icns is built on Mac (iconutil only exists on macOS)
if [[ -f "$ICONS_DIR/128x128@2x.png" ]] && [[ ! -f "$ICONS_DIR/icon.icns" ]]; then
    if command -v iconutil >/dev/null 2>&1; then
        info "Building .icns via iconutil…"
        TMP_ICONSET=$(mktemp -d)/icon.iconset
        mkdir -p "$TMP_ICONSET"
        # Build all required sizes from the largest source we have
        # (icon.png is 512, we scale down for others)
        SRC="$ICONS_DIR/icon.png"
        if [[ -f "$SRC" ]]; then
            sips -z 16 16      "$SRC" --out "$TMP_ICONSET/icon_16x16.png"         >/dev/null 2>&1
            sips -z 32 32      "$SRC" --out "$TMP_ICONSET/icon_16x16@2x.png"      >/dev/null 2>&1
            sips -z 32 32      "$SRC" --out "$TMP_ICONSET/icon_32x32.png"         >/dev/null 2>&1
            sips -z 64 64      "$SRC" --out "$TMP_ICONSET/icon_32x32@2x.png"      >/dev/null 2>&1
            sips -z 128 128    "$SRC" --out "$TMP_ICONSET/icon_128x128.png"       >/dev/null 2>&1
            sips -z 256 256    "$SRC" --out "$TMP_ICONSET/icon_128x128@2x.png"    >/dev/null 2>&1
            sips -z 256 256    "$SRC" --out "$TMP_ICONSET/icon_256x256.png"       >/dev/null 2>&1
            sips -z 512 512    "$SRC" --out "$TMP_ICONSET/icon_256x256@2x.png"    >/dev/null 2>&1
            sips -z 512 512    "$SRC" --out "$TMP_ICONSET/icon_512x512.png"       >/dev/null 2>&1
            cp "$SRC" "$TMP_ICONSET/icon_512x512@2x.png"
            iconutil -c icns -o "$ICONS_DIR/icon.icns" "$TMP_ICONSET" 2>/dev/null && ok ".icns built"
        fi
        rm -rf "$(dirname "$TMP_ICONSET")"
    fi
fi

# ---- Build the .app ---------------------------------------------------------

heading "6/7  Building the app (first build takes 5-15 min)"

info "Running tauri build…"
# Build the full .app bundle (not just the raw binary)
npx tauri build --bundles app

# ---- Install to ~/Applications ----------------------------------------------

heading "7/7  Installing"

# Tauri puts the .app in src-tauri/target/release/bundle/macos/
# On Apple Silicon the path can vary — find it dynamically
BUILT_APP=$(find "$APP_ROOT/src-tauri/target" -type d -name "$APP_NAME.app" 2>/dev/null | head -1)
# Fallback: Tauri uses the binary name from Cargo.toml which is broll-buddy-app
if [[ -z "$BUILT_APP" ]]; then
    BUILT_APP=$(find "$APP_ROOT/src-tauri/target" -type d -name "broll-buddy-app.app" 2>/dev/null | head -1)
fi

if [[ -z "$BUILT_APP" ]]; then
    fail "Build succeeded but .app not found. Check:"
    echo "   $APP_ROOT/src-tauri/target/release/bundle/"
    exit 1
fi

mkdir -p "/Applications" 2>/dev/null || true
DEST_SYS="/Applications/$APP_NAME.app"
DEST_USER="$HOME/Applications/$APP_NAME.app"

# Prefer /Applications if writable (standard Mac app location, visible
# in Finder sidebar). Fall back to ~/Applications if not.
if [[ -w "/Applications" ]] || sudo -n test -w "/Applications" 2>/dev/null; then
    DEST="$DEST_SYS"
    INSTALL_LOCATION="system"
elif [[ -w "/Applications" ]] || [[ -w "$HOME/Applications" ]] || mkdir -p "$HOME/Applications" 2>/dev/null; then
    DEST="$DEST_USER"
    INSTALL_LOCATION="user"
else
    fail "Cannot write to /Applications or ~/Applications"
    exit 1
fi

# Remove old install if present
[[ -e "$DEST" ]] && rm -rf "$DEST"

# Drop 4.41 rebrand: remove any legacy-named installs that used to live
# alongside this one (previous "B-Roll Buddy.app" from pre-rebrand builds).
for legacy in "${LEGACY_APP_NAMES[@]}"; do
    for legacy_dest in "/Applications/$legacy.app" "$HOME/Applications/$legacy.app"; do
        if [[ -e "$legacy_dest" ]]; then
            info "Removing legacy install at $legacy_dest"
            if [[ -w "$(dirname "$legacy_dest")" ]]; then
                rm -rf "$legacy_dest" 2>/dev/null || true
            else
                sudo rm -rf "$legacy_dest" 2>/dev/null || true
            fi
        fi
    done
done

if [[ "$INSTALL_LOCATION" == "system" ]] && [[ ! -w "/Applications" ]]; then
    info "Installing to /Applications requires your password…"
    sudo cp -R "$BUILT_APP" "$DEST"
else
    cp -R "$BUILT_APP" "$DEST"
fi
ok "Installed to $DEST"

echo
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Build complete${NC}"
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo
echo -e "  Open ${BOLD}$APP_NAME${NC} from:"
echo -e "    ${DIM}$DEST${NC}"
echo
echo -e "  Or run directly:"
echo -e "    ${DIM}open '$DEST'${NC}"
echo
echo -e "  The app loads the Python backend from:"
echo -e "    ${DIM}$APP_ROOT/python_backend/${NC}"
echo
echo -e "  Set your Anthropic API key inside the app (click the badge in"
echo -e "  the top-right corner). No more launchctl env var dance required."
echo
echo -e "  If you move the project folder, rebuild with:"
echo -e "    ${DIM}$SCRIPT_DIR/install.sh${NC}"
echo
