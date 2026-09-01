"""posthouse.harvest.proxy_manager — re-export of PreCut's proxy generator.

Provenance: the TOP-LEVEL ``proxy_manager`` module (it lives directly in
``python_backend/``, not inside the ``precut_pipeline`` package — hence
``import_precut("proxy_manager")`` below rather than a
``precut_pipeline.`` prefix) at the pin recorded in
``posthouse/PRECUT_PIN``. ROADMAP.md's role→skill map lists proxy
generation as an "A — repackage" harvest for the Assistant Editor
station, and Phase 1's exit criterion explicitly names a standalone proxy
wrapper.

Verified importable with nothing beyond the standard library
(``subprocess``, ``threading``, ``concurrent.futures`` — it shells out to
ffmpeg rather than binding it, which is exactly why it's light). Calling
:func:`generate_proxies_streaming` still requires ffmpeg on PATH or at a
common install location (:func:`find_ffmpeg` looks in both) — that's an
external binary dependency, not a Python one, and this environment has
ffmpeg installed.
"""
from posthouse.precut_bridge import import_precut

_mod = import_precut("proxy_manager")

find_ffmpeg = _mod.find_ffmpeg
ProxyJob = _mod.ProxyJob
generate_proxies_streaming = _mod.generate_proxies_streaming
VIDEO_EXTENSIONS = _mod.VIDEO_EXTENSIONS
AUDIO_EXTENSIONS = _mod.AUDIO_EXTENSIONS
UNSUPPORTED_EXTENSIONS = _mod.UNSUPPORTED_EXTENSIONS

__all__ = [
    "find_ffmpeg",
    "ProxyJob",
    "generate_proxies_streaming",
    "VIDEO_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "UNSUPPORTED_EXTENSIONS",
]
