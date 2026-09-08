"""posthouse.harvest.camera_inference — re-export of PreCut's camera/source
tag inference.

Provenance: ``precut_pipeline.camera_inference`` at the pin recorded in
``posthouse/PRECUT_PIN`` (see ``posthouse.precut_bridge``). ROADMAP.md's
role→skill map lists "camera/source-type inference" as Phase 1 harvest
material feeding the Project Manager's source-folder labeling (Phase 2).

Pure path/filename pattern matching (the module's own docstring: "no
imports from the rest of the pipeline, no ML, no network"). Verified
importable with nothing beyond the standard library in a clean
subprocess.
"""
from posthouse.precut_bridge import import_precut

_mod = import_precut("precut_pipeline.camera_inference")

infer_camera_tags = _mod.infer_camera_tags

__all__ = ["infer_camera_tags"]
