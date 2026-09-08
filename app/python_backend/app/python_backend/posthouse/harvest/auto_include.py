"""posthouse.harvest.auto_include — re-export of PreCut's auto-include rules.

Provenance: ``precut_pipeline.auto_include`` at the pin recorded in
``posthouse/PRECUT_PIN`` (see ``posthouse.precut_bridge``). ROADMAP.md
maps this to Phase 2's "Default Includes → brand-asset staging": the
Project Manager will use it to stage recurring per-project assets (SFX,
logos, LUTs) the way PreCut's own auto-include settings do today.

Verified importable with nothing beyond the Python 3.11 standard library
(``dataclasses``, ``pathlib`` — confirmed by running
``import precut_pipeline.auto_include`` in a clean subprocess with no
stubs and no ML packages installed). Nothing here needs
``posthouse.precut_bridge``'s marker-dependency stub trick.
"""
from posthouse.precut_bridge import import_precut

_mod = import_precut("precut_pipeline.auto_include")

AutoIncludeRule = _mod.AutoIncludeRule
kind_for_path = _mod.kind_for_path
unsupported_reason = _mod.unsupported_reason
validate_rule = _mod.validate_rule
normalize_bin_path = _mod.normalize_bin_path
expand_rule = _mod.expand_rule
expand_rules = _mod.expand_rules

__all__ = [
    "AutoIncludeRule",
    "kind_for_path",
    "unsupported_reason",
    "validate_rule",
    "normalize_bin_path",
    "expand_rule",
    "expand_rules",
]
