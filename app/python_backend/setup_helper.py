"""PreCut first-launch setup helper.

Runs under the system Python (/usr/bin/python3) so it works even
BEFORE any third-party packages are installed. Only uses stdlib.

Responsibilities
----------------
1. Detect whether each required dependency is present:
     - Xcode Command Line Tools (for compilation & git)
     - Homebrew
     - ffmpeg
     - Python 3 (a version new enough for our pip deps; prefer
       a non-system Python so we don't need --break-system-packages)
     - Python packages from requirements.txt (imported under the
       Python we picked above, not under us)

2. Install missing dependencies on request, one at a time.

3. Verify the install completed by re-running the check.

4. Persist a "setup_complete: true" flag in the app settings
   once all checks pass, so the main app can skip the screen.

Protocol
--------
Invoked with a single subcommand as argv[1]:

    python3 setup_helper.py check
        -> prints a single JSON object to stdout with state of
           every dependency, then exits 0.

    python3 setup_helper.py install <component>
        -> streams JSONL progress events to stdout:
             {"type": "install_progress", "component": "...",
              "stage": "...", "message": "...", "pct": 0-100}
             {"type": "install_done", "component": "...", "ok": true}
             {"type": "install_error", "component": "...",
              "message": "..."}
           Exits 0 on success, non-zero on failure.

    python3 setup_helper.py mark-complete
        -> writes setup_complete=True to settings.json, exits 0.

    python3 setup_helper.py reset
        -> removes setup_complete from settings.json so the
           setup screen appears again on next launch.

Why a separate script?
----------------------
The main backend.py imports anthropic, open-clip, whisper, etc.
during startup — none of which may be installed on first launch.
This helper deliberately uses stdlib only so it can run in the
worst-case "nothing but macOS system Python" environment.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Event protocol — one line of JSON to stdout per event. Rust reads these
# with BufReader::lines, same as for backend.py.
# ---------------------------------------------------------------------------

def _emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def _progress(component: str, stage: str, message: str, pct: int | None = None) -> None:
    ev = {
        "type": "install_progress",
        "component": component,
        "stage": stage,
        "message": message,
    }
    if pct is not None:
        ev["pct"] = pct
    _emit(ev)


def _done(component: str, ok: bool, message: str = "") -> None:
    _emit({
        "type": "install_done" if ok else "install_error",
        "component": component,
        "ok": ok,
        "message": message,
    })


# ---------------------------------------------------------------------------
# Paths — mirror settings.py logic but WITHOUT importing project.py, which
# pulls in third-party deps transitively.
# ---------------------------------------------------------------------------

def _app_support_dir() -> Path:
    # "Post House", not "PreCut" -- see project.py's app_support_dir()
    # docstring. Must stay in sync with that path.
    base = Path.home() / "Library" / "Application Support" / "Post House"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _settings_path() -> Path:
    return _app_support_dir() / "settings.json"


def _load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(data: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    # Match settings.py: 0600 — owner rw only. The file may later hold
    # an API key, and we want restrictive perms from the very first write.
    import stat
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Detection — each returns a dict {present: bool, detail: str, ...}
# ---------------------------------------------------------------------------

# macOS path additions: GUI-launched apps don't inherit shell PATH, so we
# search these common locations explicitly for binaries.
_PATH_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


def _which(binary: str) -> str | None:
    """Like shutil.which, but searches the macOS-standard locations even
    when the current process doesn't have them on PATH. Returns the full
    path if found, else None."""
    # Check inherited PATH first
    found = shutil.which(binary)
    if found:
        return found
    # Fall back to our explicit list
    for d in _PATH_DIRS:
        candidate = Path(d) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _check_xcode_clt() -> dict:
    """Xcode Command Line Tools installed? We use `xcode-select -p` which
    prints the active developer dir if set. This is required for clang
    (building Python wheels if needed) and for git."""
    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return {"present": True, "detail": result.stdout.strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {"present": False, "detail": "not installed"}


def _check_homebrew() -> dict:
    """Homebrew: check for /opt/homebrew/bin/brew (Apple Silicon) or
    /usr/local/bin/brew (Intel). Needed to install ffmpeg cleanly."""
    brew = _which("brew")
    if not brew:
        return {"present": False, "detail": "not installed"}
    try:
        result = subprocess.run(
            [brew, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            first_line = result.stdout.strip().split("\n")[0]
            return {"present": True, "detail": first_line, "path": brew}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {"present": False, "detail": "found but not runnable"}


def _check_ffmpeg() -> dict:
    """ffmpeg on PATH (or brew-installed)."""
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        return {"present": False, "detail": "not installed"}
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # First line looks like: "ffmpeg version 6.1 Copyright ..."
            first_line = result.stdout.strip().split("\n")[0]
            return {"present": True, "detail": first_line, "path": ffmpeg}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {"present": False, "detail": "found but not runnable"}


_CEP_EXTENSION_ID = "com.posthouse.interpret"
# CSXS runtime versions across recent Premiere releases (2019-2026ish).
# We enable debug mode for all of them since we can't know which one
# Ryan's installed Premiere actually uses, and setting it for a version
# he doesn't have is a harmless no-op default write.
_CEP_DEBUG_VERSIONS = [8, 9, 10, 11, 12]


def _cep_extensions_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Adobe" / "CEP" / "extensions"


def _premiere_extension_source() -> Path:
    """The bundled premiere_extension/ directory, sibling to python_backend/
    both in dev (this repo) and in a packaged build (tauri.conf.json bundles
    it alongside python_backend under the same resource root)."""
    return Path(__file__).resolve().parent.parent / "premiere_extension"


def _check_premiere_extension() -> dict:
    """Not required for all_ready — Ryan may not even have Premiere on this
    Mac. Purely informational, like homebrew's own check."""
    source = _premiere_extension_source()
    if not source.exists():
        return {"present": False, "detail": f"bundled extension not found at {source}"}

    link = _cep_extensions_dir() / _CEP_EXTENSION_ID
    if not link.exists():
        return {"present": False, "detail": "not installed into Adobe CEP extensions"}
    try:
        installed_correctly = link.is_symlink() and link.resolve() == source.resolve()
    except OSError:
        installed_correctly = False
    if not installed_correctly:
        return {"present": False, "detail": f"{link} exists but doesn't point at the current bundled copy"}

    debug_on = any(_cep_debug_mode_enabled(v) for v in _CEP_DEBUG_VERSIONS)
    if not debug_on:
        return {"present": False, "detail": "installed, but PlayerDebugMode isn't enabled for any CSXS version"}

    return {"present": True, "detail": "installed and enabled"}


def _cep_debug_mode_enabled(csxs_version: int) -> bool:
    try:
        result = subprocess.run(
            ["defaults", "read", f"com.adobe.CSXS.{csxs_version}", "PlayerDebugMode"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "1"


def _check_python() -> dict:
    """Find a Python 3.10+ suitable for backend deps.

    Preference order:
      1. Homebrew python3 at /opt/homebrew/bin/python3 (Apple Silicon)
         or /usr/local/bin/python3 (Intel) — these are NOT
         'externally-managed' in a way that blocks pip without flags.
      2. System /usr/bin/python3 — works but requires
         --break-system-packages for pip installs on macOS 14+.

    We return the path of the chosen interpreter plus its version.
    Required: 3.10 minimum (open-clip-torch wheels on macOS arm64
    don't go back further reliably).
    """
    candidates = [
        "/opt/homebrew/bin/python3",   # arm64 Homebrew — preferred
        "/usr/local/bin/python3",      # Intel Homebrew
        "/usr/bin/python3",            # macOS system Python
    ]
    for candidate in candidates:
        if not Path(candidate).exists():
            continue
        try:
            result = subprocess.run(
                [candidate, "-c",
                 "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                continue
            version = result.stdout.strip()
            major, minor = version.split(".")[:2]
            if (int(major), int(minor)) >= (3, 10):
                return {
                    "present": True,
                    "detail": f"Python {version}",
                    "path": candidate,
                    "version": version,
                    "is_system": candidate == "/usr/bin/python3",
                }
        except (subprocess.TimeoutExpired, ValueError):
            continue
    return {"present": False, "detail": "no Python 3.10+ found"}


# Parse the names out of requirements.txt — we need them for the "is the
# package importable under the target Python" check.
def _requirements_path() -> Path:
    # This file lives next to us in python_backend/
    return Path(__file__).parent / "requirements.txt"


def _parse_requirements() -> list[tuple[str, str]]:
    """Return a list of (package_name, import_name) for each line in
    requirements.txt. Import names differ from pip names in a few cases
    (Pillow -> PIL, openai-whisper -> whisper, etc.)."""
    # pip name -> import name overrides
    import_overrides = {
        "Pillow": "PIL",
        "openai-whisper": "whisper",
        "open-clip-torch": "open_clip",
        "ffmpeg-python": "ffmpeg",
        "audio-offset-finder": "audio_offset_finder",
    }
    out: list[tuple[str, str]] = []
    req_file = _requirements_path()
    if not req_file.exists():
        return out
    for raw in req_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Drop version specifier: "foo>=1.0" -> "foo"
        pkg = line.split(";")[0]
        for sep in (">=", "<=", "==", ">", "<", "~="):
            if sep in pkg:
                pkg = pkg.split(sep)[0]
                break
        pkg = pkg.strip()
        if not pkg:
            continue
        import_name = import_overrides.get(pkg, pkg.replace("-", "_"))
        out.append((pkg, import_name))
    return out


def _check_python_packages(python_bin: str) -> dict:
    """For each package in requirements.txt, test `python_bin -c 'import x'`.
    Returns a summary with a per-package breakdown."""
    packages = _parse_requirements()
    if not packages:
        return {"present": True, "detail": "no requirements file",
                "missing": [], "installed": []}

    missing = []
    installed = []
    for pkg, import_name in packages:
        try:
            result = subprocess.run(
                [python_bin, "-c", f"import {import_name}"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                installed.append(pkg)
            else:
                missing.append(pkg)
        except subprocess.TimeoutExpired:
            # Import hanging — count as missing so we re-install
            missing.append(pkg)

    return {
        "present": len(missing) == 0,
        "detail": f"{len(installed)}/{len(packages)} packages installed",
        "missing": missing,
        "installed": installed,
        "total": len(packages),
    }


# ---------------------------------------------------------------------------
# Public check — collect all component states into a single report.
# ---------------------------------------------------------------------------

def check_all() -> dict:
    """Run all detections and return a unified report."""
    xcode = _check_xcode_clt()
    brew = _check_homebrew()
    ffmpeg = _check_ffmpeg()
    python = _check_python()
    premiere_extension = _check_premiere_extension()

    # Python packages depend on which Python we chose
    if python["present"]:
        pyver = python.get("version", "?")
        pkgs = _check_python_packages(python["path"])
        pkgs["python_used"] = f"{python['path']} ({pyver})"
    else:
        pkgs = {
            "present": False,
            "detail": "can't check — no Python",
            "missing": [],
            "installed": [],
        }

    # Overall ready state: everything critical is present.
    # Homebrew is "recommended, not required" because ffmpeg could be
    # installed some other way.
    all_ok = (
        xcode["present"]
        and ffmpeg["present"]
        and python["present"]
        and pkgs["present"]
    )

    settings = _load_settings()

    return {
        "type": "setup_check",
        "all_ready": all_ok,
        "setup_complete_flag": bool(settings.get("setup_complete")),
        "components": {
            "xcode_clt": xcode,
            "homebrew": brew,
            "ffmpeg": ffmpeg,
            "python": python,
            "python_packages": pkgs,
            "premiere_extension": premiere_extension,
        },
    }


# ---------------------------------------------------------------------------
# Install runners — one per installable component.
# ---------------------------------------------------------------------------

def _run_streaming(cmd: list[str], component: str, stage: str,
                   env: dict | None = None, cwd: str | None = None) -> int:
    """Run a subprocess and emit each line of its stdout/stderr as a
    progress event. Returns the exit code."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    # Ensure the child sees the standard macOS PATH. GUI apps on macOS
    # don't inherit shell PATH, so Homebrew is invisible without this.
    current_path = full_env.get("PATH", "")
    extras = ":".join(_PATH_DIRS)
    full_env["PATH"] = f"{extras}:{current_path}" if current_path else extras

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=full_env,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        _progress(component, stage, f"command not found: {e}")
        return 127

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line:
            _progress(component, stage, line)
    return proc.wait()


def install_xcode_clt() -> int:
    """Trigger the Apple-provided CLT installer. This opens a GUI dialog
    and returns immediately — we then poll until xcode-select -p succeeds.
    If the user cancels, we eventually time out.
    """
    component = "xcode_clt"
    _progress(component, "trigger", "Triggering Xcode Command Line Tools installer…")

    # This command pops up the system dialog if CLT is missing.
    # It returns quickly (non-zero if already installed, which is fine).
    subprocess.run(["xcode-select", "--install"],
                   capture_output=True, text=True)

    _progress(component, "wait",
              "Waiting for install to complete (accept the dialog that just appeared)…")

    # Poll up to 30 min — CLT is a big download.
    deadline = time.time() + (30 * 60)
    last_msg = 0.0
    while time.time() < deadline:
        check = _check_xcode_clt()
        if check["present"]:
            _done(component, True, f"installed at {check['detail']}")
            return 0
        # Heartbeat every 5 sec so the UI knows we're still waiting
        now = time.time()
        if now - last_msg > 5:
            elapsed = int(now - (deadline - 30 * 60))
            _progress(component, "wait",
                      f"Still waiting… ({elapsed}s elapsed — dialog must be accepted)")
            last_msg = now
        time.sleep(1)

    _done(component, False,
          "Timed out waiting for Command Line Tools install. "
          "Please install manually by running `xcode-select --install` in Terminal, "
          "then relaunch PreCut.")
    return 1


def _is_admin_user() -> bool:
    """Return True if the current macOS user is in the 'admin' group.

    Homebrew genuinely cannot install on a non-admin account — its
    installer needs sudo to chown and chmod various directories. We
    check this before running the installer so we can fail fast with
    a clearer error message than Homebrew's "Need sudo access (e.g.
    the user X needs to be an Administrator)!" which beta testers
    have hit.

    Drop 1.0.0-beta.3: added in response to a tester whose account
    wasn't an admin — see the install setup-helper bug report.
    """
    try:
        rc = subprocess.run(
            ["dseditgroup", "-o", "checkmember", "-m",
             os.environ.get("USER", ""), "admin"],
            capture_output=True, text=True, timeout=5,
        ).returncode
        return rc == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # dseditgroup absent or hung — assume admin and let Homebrew
        # fail with its own message rather than blocking install.
        return True


def install_homebrew() -> int:
    """Run the official Homebrew installer non-interactively.

    We invoke the upstream script with NONINTERACTIVE=1 which skips the
    confirmation prompt but still requires admin password (sudo). On a
    fresh Mac, the password is prompted inside the script — if the user
    didn't grant accessibility/automation perms we can't forward the
    prompt cleanly, so the fallback is clear error messaging.
    """
    component = "homebrew"

    # Drop 1.0.0-beta.3: pre-check admin status. Homebrew's installer
    # requires sudo, which requires admin group membership. A
    # non-admin tester's only feedback was "FAILED" with no way to
    # diagnose the real cause — surface it directly here.
    if not _is_admin_user():
        _done(component, False,
              "Cannot install Homebrew: your macOS account is not an "
              "administrator. Sign in as an admin account, or have one "
              "add your account to the admin group in "
              "System Settings → Users & Groups.")
        return 1

    _progress(component, "download", "Downloading Homebrew install script…")

    # Two-step so we get a clearer error if the script fetch fails:
    # 1) curl the script to a temp file
    # 2) run it with bash
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".sh", delete=False
        ) as script_file:
            script_path = script_file.name

        fetch = subprocess.run(
            ["curl", "-fsSL",
             "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh",
             "-o", script_path],
            capture_output=True, text=True, timeout=60,
        )
        if fetch.returncode != 0:
            _done(component, False,
                  f"Couldn't download Homebrew installer: {fetch.stderr.strip()}")
            return fetch.returncode

        _progress(component, "install",
                  "Running Homebrew installer — you'll be prompted for your Mac password.")

        # NONINTERACTIVE=1 skips the "press RETURN to continue" prompt
        # but sudo will still prompt in the Terminal-less GUI context.
        # We run via `open -a Terminal` to give the user a visible
        # password prompt. See below.
        rc = _run_homebrew_in_terminal(script_path)

        try:
            os.unlink(script_path)
        except OSError:
            pass

        if rc == 0:
            # Verify install
            brew_check = _check_homebrew()
            if brew_check["present"]:
                _done(component, True, brew_check["detail"])
                return 0
            else:
                _done(component, False,
                      "Homebrew installer returned success but `brew` is not on PATH.")
                return 1
        else:
            _done(component, False, f"Homebrew installer exited with code {rc}")
            return rc
    except subprocess.TimeoutExpired:
        _done(component, False, "Timed out downloading Homebrew installer.")
        return 1


def _run_homebrew_in_terminal(script_path: str) -> int:
    """Open Terminal.app with the Homebrew installer. The user interacts
    with the password prompt in that Terminal window; we poll for
    completion by checking whether `brew` becomes available.

    This is the cleanest path for a GUI-launched .app — attempting to
    forward a sudo prompt through a pipe to a React UI is not practical
    and has surprising permission-wall failures.
    """
    _progress("homebrew", "launch_terminal",
              "Opening Terminal window — approve the Homebrew prompts there.")

    # Create a little wrapper script that runs the installer and then
    # signals completion via a sentinel file. We poll the sentinel.
    sentinel = _app_support_dir() / ".homebrew_done"
    try:
        sentinel.unlink()
    except FileNotFoundError:
        pass
    sentinel.parent.mkdir(parents=True, exist_ok=True)

    wrapper_code = f'''#!/bin/bash
# Homebrew installer wrapper used by PreCut's first-launch setup.
#
# IMPORTANT: do NOT export NONINTERACTIVE here. With NONINTERACTIVE=1
# Homebrew's installer uses non-interactive sudo (sudo -nv) which
# fails immediately if no cached credential exists, producing the
# misleading "Need sudo access on macOS (e.g. the user X needs to
# be an Administrator)!" error even for admin users who simply
# haven't recently entered their password.
#
# Inside a Terminal.app window we have a TTY, so the installer's
# "Press RETURN to continue" prompt is fine — the user just presses
# Return. The sudo prompt is also visible and works normally.
echo "Running Homebrew installer..."
echo "You will be asked to:"
echo "  1) press RETURN to continue, then"
echo "  2) enter your Mac password to grant admin access."
echo ""

# Quick admin-group precheck. Homebrew genuinely cannot install on a
# non-admin Mac, so fail early with a clearer explanation than the
# upstream error gives.
if ! dseditgroup -o checkmember -m "$USER" admin > /dev/null 2>&1 ; then
    echo ""
    echo "Homebrew install FAILED — your macOS account ($USER) is not"
    echo "an administrator. Homebrew requires admin access to install."
    echo ""
    echo "To fix:"
    echo "  - Sign in as an administrator account, OR"
    echo "  - Have an administrator add your account to the admin group"
    echo "    (System Settings → Users & Groups)."
    echo ""
    echo "You can close this window."
    echo 1 > "{sentinel}"
    exit 1
fi

/bin/bash "{script_path}"
rc=$?
echo $rc > "{sentinel}"
if [ $rc -eq 0 ]; then
    echo ""
    echo "Homebrew install complete. You can close this window."
else
    echo ""
    echo "Homebrew install FAILED (exit code $rc). You can close this window."
fi
'''
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False
    ) as f:
        f.write(wrapper_code)
        wrapper_path = f.name
    os.chmod(wrapper_path, 0o755)

    # Use osascript to tell Terminal to run our wrapper in a new window.
    # This gives the user a visible password prompt.
    apple_script = (
        f'tell application "Terminal" to activate\n'
        f'tell application "Terminal" to do script "{wrapper_path}"'
    )
    subprocess.run(["osascript", "-e", apple_script],
                   capture_output=True, text=True)

    # Poll for the sentinel file to appear
    deadline = time.time() + (20 * 60)  # 20 min cap
    last_heartbeat = 0.0
    while time.time() < deadline:
        if sentinel.exists():
            try:
                rc = int(sentinel.read_text().strip())
            except (ValueError, OSError):
                rc = 1
            try:
                sentinel.unlink()
                os.unlink(wrapper_path)
            except OSError:
                pass
            return rc
        now = time.time()
        if now - last_heartbeat > 10:
            _progress("homebrew", "waiting",
                      "Still waiting for Homebrew installer to finish in Terminal…")
            last_heartbeat = now
        time.sleep(2)

    # Timed out
    try:
        os.unlink(wrapper_path)
    except OSError:
        pass
    return 124  # standard timeout exit code


def install_ffmpeg() -> int:
    """`brew install ffmpeg`. Assumes Homebrew is already present."""
    component = "ffmpeg"
    brew = _which("brew")
    if not brew:
        _done(component, False,
              "Homebrew is required to install ffmpeg. Install Homebrew first.")
        return 1
    _progress(component, "start", f"Running: {brew} install ffmpeg")
    rc = _run_streaming([brew, "install", "ffmpeg"], component, "brew")
    if rc == 0:
        check = _check_ffmpeg()
        if check["present"]:
            _done(component, True, check["detail"])
            return 0
    _done(component, False, f"brew install ffmpeg failed (exit {rc})")
    return rc


def install_python() -> int:
    """Install Python 3.12 via Homebrew. Assumes Homebrew is present.

    We pin to a specific major/minor (3.12) rather than just `python3`
    so we get a predictable version. Homebrew's `python@3.12` formula
    installs to /opt/homebrew/bin/python3.12 with a symlink at
    /opt/homebrew/bin/python3.
    """
    component = "python"
    brew = _which("brew")
    if not brew:
        _done(component, False,
              "Homebrew is required to install Python. Install Homebrew first.")
        return 1
    _progress(component, "start", f"Running: {brew} install python@3.12")
    rc = _run_streaming([brew, "install", "python@3.12"], component, "brew")
    if rc == 0:
        check = _check_python()
        if check["present"]:
            _done(component, True, check["detail"])
            return 0
    _done(component, False, f"brew install python@3.12 failed (exit {rc})")
    return rc


def install_python_packages() -> int:
    """pip install -r requirements.txt into the Python we picked.

    This is the long one — torch + open-clip + whisper + lancedb
    combined are ~500MB of downloads. The React UI watches the stream
    and shows individual lines (pip already prints %-progress per wheel).
    """
    component = "python_packages"
    python = _check_python()
    if not python["present"]:
        _done(component, False, "No suitable Python found. Install Python first.")
        return 1
    python_bin = python["path"]

    req_file = _requirements_path()
    if not req_file.exists():
        _done(component, False, f"requirements.txt not found at {req_file}")
        return 1

    # Build pip command. We use --user to install into the user's
    # site-packages rather than the Python install's site-packages — this
    # keeps us OUT of system directories even if the user picked
    # /usr/bin/python3, and means we don't need sudo.
    pip_cmd = [
        python_bin, "-m", "pip", "install",
        "--user",
        "--upgrade",
        "-r", str(req_file),
    ]
    # /usr/bin/python3 on macOS 14+ is "externally managed" (PEP 668)
    # and refuses pip installs without this flag. Homebrew Python is
    # fine without it but the flag is harmless either way.
    if python.get("is_system"):
        pip_cmd.append("--break-system-packages")

    _progress(component, "pip",
              f"Installing Python packages into {python_bin}… (this can take 5-10 min)")
    rc = _run_streaming(pip_cmd, component, "pip")

    if rc == 0:
        check = _check_python_packages(python_bin)
        if check["present"]:
            _done(component, True, f"{check['total']} packages installed")
            return 0
        else:
            missing = ", ".join(check["missing"])
            _done(component, False,
                  f"pip exited 0 but some packages still missing: {missing}")
            return 1

    _done(component, False, f"pip install failed (exit {rc})")
    return rc


def install_premiere_extension() -> int:
    """Symlink premiere_extension/ into Adobe's CEP extensions folder and
    enable PlayerDebugMode so Premiere loads it unsigned. Idempotent —
    replaces a stale symlink from a prior install, leaves everything else
    in that folder (other extensions) untouched.

    Not fatal if Premiere isn't installed on this Mac at all: this just
    stages the extension for whenever it is; Premiere itself only reads
    its CEP extensions folder at launch.
    """
    component = "premiere_extension"
    source = _premiere_extension_source()
    if not source.exists():
        _done(component, False, f"bundled extension not found at {source}")
        return 1

    dest_dir = _cep_extensions_dir()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _done(component, False, f"couldn't create {dest_dir}: {e}")
        return 1

    link = dest_dir / _CEP_EXTENSION_ID
    _progress(component, "symlink", f"Linking {link} -> {source}")
    try:
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                # A real directory here (not our symlink) is someone else's
                # install of the same id — don't clobber it silently.
                _done(component, False,
                      f"{link} is a real directory, not a symlink — remove it manually first")
                return 1
            link.unlink()
        link.symlink_to(source)
    except OSError as e:
        _done(component, False, f"couldn't create symlink: {e}")
        return 1

    for v in _CEP_DEBUG_VERSIONS:
        _progress(component, "debug-mode", f"Enabling PlayerDebugMode for CSXS.{v}")
        subprocess.run(
            ["defaults", "write", f"com.adobe.CSXS.{v}", "PlayerDebugMode", "1"],
            capture_output=True, text=True, timeout=5,
        )

    check = _check_premiere_extension()
    if check["present"]:
        _done(component, True,
              "Installed. Fully quit and reopen Premiere Pro once to pick it up.")
        return 0
    _done(component, False, f"Installed but verification failed: {check['detail']}")
    return 1


# ---------------------------------------------------------------------------
# Setup-complete flag — persisted in settings.json next to the API key.
# ---------------------------------------------------------------------------

def mark_setup_complete() -> int:
    settings = _load_settings()
    settings["setup_complete"] = True
    _save_settings(settings)
    _emit({"type": "setup_complete_marked", "ok": True})
    return 0


def reset_setup() -> int:
    settings = _load_settings()
    settings.pop("setup_complete", None)
    _save_settings(settings)
    _emit({"type": "setup_reset", "ok": True})
    return 0


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

_INSTALLERS = {
    "xcode_clt": install_xcode_clt,
    "homebrew": install_homebrew,
    "ffmpeg": install_ffmpeg,
    "python": install_python,
    "python_packages": install_python_packages,
    "premiere_extension": install_premiere_extension,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(json.dumps({"type": "error",
                          "message": "usage: setup_helper.py <check|install <component>|mark-complete|reset>"}),
              file=sys.stderr)
        return 2

    cmd = argv[1]

    if cmd == "check":
        report = check_all()
        _emit(report)
        return 0

    if cmd == "install":
        if len(argv) < 3:
            _emit({"type": "error", "message": "install requires a component name"})
            return 2
        component = argv[2]
        installer = _INSTALLERS.get(component)
        if installer is None:
            _emit({"type": "error", "message": f"unknown component: {component}"})
            return 2
        return installer()

    if cmd == "mark-complete":
        return mark_setup_complete()

    if cmd == "reset":
        return reset_setup()

    _emit({"type": "error", "message": f"unknown command: {cmd}"})
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
