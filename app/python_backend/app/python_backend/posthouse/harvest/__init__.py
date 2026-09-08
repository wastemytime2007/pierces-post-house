"""posthouse.harvest — thin, pinned wrappers around light-dependency
PreCut capabilities.

Every module in this package is a re-export: it imports the real
capability from PreCut through :mod:`posthouse.precut_bridge` (door 3)
and adds nothing but a docstring stating what it is, where it comes from,
and the commit it's pinned against. None of these need PreCut's ML venv
(torch, whisper, lancedb, open_clip, anthropic) — see each module's
docstring for how that was verified, and ``posthouse/harvest/DEFERRED.md``
for the capabilities that DO need it and are deliberately not wrapped
here.
"""
