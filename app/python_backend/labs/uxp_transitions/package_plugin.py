#!/usr/bin/env python3
"""Package the Transition Lab as a .ccx and install it with Adobe's own installer, so Premiere (release and Beta, this version and later) loads it at startup without the UXP Developer Tools.

    python3 labs/uxp_transitions/package_plugin.py            package and install
    python3 labs/uxp_transitions/package_plugin.py --remove   uninstall (Adobe's installer, by plugin name)

A .ccx is a zip of the plugin folder. The manifest's host is "premierepro" with only a minimum version (25.6.0), so any later Premiere, including the Beta, may load it.
"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPIA = "/Library/Application Support/Adobe/Adobe Desktop Common/RemoteComponents/UPI/UnifiedPluginInstallerAgent/UnifiedPluginInstallerAgent.app/Contents/MacOS/UnifiedPluginInstallerAgent"
FILES = ["manifest.json", "index.html", "index.js"]


def run(args):
    r = subprocess.run([UPIA] + args, capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    manifest = json.loads((HERE / "manifest.json").read_text())
    if "--remove" in sys.argv:
        print(run(["--remove", manifest["name"]]))
        return 0
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    ccx = dist / f"{manifest['id']}_{manifest['version']}.ccx"
    with zipfile.ZipFile(ccx, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(HERE / f, f)
    print("packaged", ccx, ccx.stat().st_size, "bytes")
    code, out = run(["--install", str(ccx)])
    print("install exit", code)
    print(out[-2000:])
    code, out = run(["--list", "all"])
    lines = out.splitlines()
    for i, l in enumerate(lines):
        if "Premiere" in l:
            print("\n".join(lines[i:i + 8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
