"""posthouse.harvest.theme_categories — re-export of PreCut's theme/room
category taxonomy.

Provenance: ``precut_pipeline.theme_categories`` at the pin recorded in
``posthouse/PRECUT_PIN`` (see ``posthouse.precut_bridge``). Already part
of the Phase 0 safety net's stdlib-only exporter chain
(``safety_net/tests/test_import_gate.py`` CHAIN_MODULES); re-exported
here so the rest of ``posthouse`` (e.g. a future subject-grouping skill,
ROADMAP.md Phase 4) can use the same taxonomy without a second import
path into PreCut. Ryan's real-estate-tuned vocabulary is a Phase 4 open
question (ARCHITECTURE.md "taxonomy width") — not addressed by this
wrapper.

Verified importable with nothing beyond the standard library
(``dataclasses``, ``typing``).
"""
from posthouse.precut_bridge import import_precut

_mod = import_precut("precut_pipeline.theme_categories")

ThemeCategory = _mod.ThemeCategory
THEME_CATEGORIES = _mod.THEME_CATEGORIES
categorize_tag = _mod.categorize_tag
categorize_tags = _mod.categorize_tags
get_category = _mod.get_category

__all__ = [
    "ThemeCategory",
    "THEME_CATEGORIES",
    "categorize_tag",
    "categorize_tags",
    "get_category",
]
