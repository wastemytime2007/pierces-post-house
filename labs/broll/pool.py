"""Build a searchable B-roll pool from folders of finished videos and images (standalone, file in / file out, nothing in app/).

    python3 labs/broll/pool.py --src "~/Desktop/Portfolio Videos" [--src "<site captures folder>"] --out "<pool folder>" [--every 3]

For every video one frame every `--every` seconds is saved (448 px wide) and every image is saved as one frame; each frame is embedded with the same
CLIP model PreCut uses for B-roll (laion ViT-B-32, from the local cache, so nothing is downloaded). Written: `frames/*.jpg`, `embeddings.npy`, `pool.json`
(source file, time, frame file, `kind`, and what was left out and why).

What is left out, and said so in pool.json: full-length movies (over 20 minutes, or named like a film release), project / photoshop / audio files, and a
second copy of the same stem (a .mov next to the same-named .mp4). Only the folder's top level is read: sub-folders such as `_selects` are not (a person
chose what goes in a pool, not a crawler). Everything is a still frame from a finished video: a clip placed from here is a candidate, never a decision."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MOVIE_MAX_SEC = 20 * 60
FILM_NAME = re.compile(r"YIFY|BluRay|BRRip|WEB-?DL|x264|x265|\bVPPV\b", re.I)
MODEL_NAME, MODEL_TAG = "ViT-B-32", "laion2b_s34b_b79k"                 # the weights PreCut already caches (the .bin only, so loaded by name)
MODEL_ID = f"{MODEL_NAME}/{MODEL_TAG}"


class PoolError(Exception):
    pass


def probe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def classify(path: Path, duration: float | None = None, movie_max: float = MOVIE_MAX_SEC) -> tuple[str, str]:
    """('video'|'image'|'skip', reason). Pure apart from the optional duration probe, so it is testable."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image", ""
    if ext not in VIDEO_EXT:
        return "skip", f"{ext or 'no extension'} is not a video or image"
    if FILM_NAME.search(path.stem):
        return "skip", "named like a film release (not footage Ryan made)"
    d = probe_duration(path) if duration is None else duration
    if movie_max and d > movie_max:
        return "skip", f"{d / 60:.0f} minutes long: a full-length movie, not B-roll"
    if d <= 0:
        return "skip", "could not read its duration"
    return "video", ""


CACHE_DIRS = ("Adobe Premiere Pro", "proxies", "Proxies", "Auto-Save", "Audio Previews", "Captured and Generated")


def tree_files(root: Path, skip: tuple[str, ...] = (), keep_proxies: bool = False) -> list[Path]:
    """Every file under `root`, depth first, except hidden files and folders, editor cache folders, and folders whose name matches one of `skip` (case-insensitive substring)."""
    out = []
    for dp, dn, fn in os.walk(root):
        caches = tuple(c for c in CACHE_DIRS if not (keep_proxies and c.lower() == "proxies"))
        dn[:] = sorted(d for d in dn if not d.startswith(".") and not any(c in d for c in caches) and not any(k.lower() in d.lower() for k in skip))
        out += [Path(dp) / f for f in sorted(fn)]
    return out


def list_sources(srcs: list[Path], trees: list[Path] | None = None, skip: tuple[str, ...] = (), keep_proxies: bool = False, movie_max: float = MOVIE_MAX_SEC) -> tuple[list[dict], list[dict]]:
    """`srcs` are read one level deep (videos and images); `trees` are walked all the way down but only VIDEOS are taken (a folder tree of a business holds
    QR codes, banners and layered files that are not B-roll). `skip` names folders left out of a tree, and each is reported."""
    keep, left_out, seen = [], [], set()
    work = [(src, sorted(q for q in src.iterdir() if q.is_file()), False) for src in srcs] + [(t, tree_files(t, skip, keep_proxies), True) for t in (trees or [])]
    for t in trees or []:
        for dp, dn, _fn in os.walk(t):
            for d in dn:
                if any(k.lower() in d.lower() for k in skip):
                    left_out.append({"file": str(Path(dp) / d), "why": "folder skipped on purpose (named in --skip-folder)"})
    for src, files, videos_only in work:
        for p in files:
            if p.name.startswith("."):
                continue
            if videos_only and p.suffix.lower() not in VIDEO_EXT:
                continue
            if videos_only and keep_proxies and "proxies" not in [x.lower() for x in p.relative_to(src).parts[:-1]]:
                continue                                          # --proxies: the small working copies only, never the originals beside them (same footage twice)
            kind, why = classify(p, movie_max=movie_max)
            if kind == "skip":
                if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT or why.startswith(("named", "could")):
                    left_out.append({"file": str(p), "why": why})
                continue
            stem = (str(p.parent), p.stem.lower())
            if stem in seen:
                left_out.append({"file": str(p), "why": "a same-named copy is already in the pool"})
                continue
            seen.add(stem)
            keep.append({"file": str(p), "kind": kind})
    return keep, left_out


def extract_frames(item: dict, frames: Path, every: float, index: int = 0) -> list[dict]:
    p = Path(item["file"])
    base = f"{index:03d}_" + re.sub(r"[^A-Za-z0-9]+", "_", p.stem).strip("_")[:50]        # the index keeps two sources from ever sharing a frame name
    out = []
    if item["kind"] == "image":
        dst = frames / f"{base}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", "scale=448:-2", "-frames:v", "1", str(dst)], check=False)
        if dst.exists():
            out.append({"file": str(p), "time": 0.0, "frame": dst.name, "kind": "image"})
        return out
    pattern = frames / f"{base}_%05d.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", f"fps=1/{every},scale=448:-2", "-q:v", "4", str(pattern)], check=False)
    for f in sorted(frames.glob(f"{base}_[0-9][0-9][0-9][0-9][0-9].jpg")):
        n = int(f.stem[-5:])
        out.append({"file": str(p), "time": round((n - 1) * every + every / 2, 2), "frame": f.name, "kind": "video"})
    return out


_CLIP = {}


def clip():
    """(model, preprocess, tokenizer), loaded once. Offline: the weights are PreCut's already-cached ones."""
    if not _CLIP:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            import open_clip
            import torch
        except ImportError as e:
            raise PoolError(f"open_clip / torch are not installed ({e})")
        try:
            model, _, pre = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=MODEL_TAG)
        except Exception as e:                              # missing cache, no network
            raise PoolError(f"the CLIP weights are not in the local cache ({e})")
        model.eval()
        _CLIP.update(model=model, pre=pre, tok=open_clip.get_tokenizer(MODEL_NAME), torch=torch)
    return _CLIP["model"], _CLIP["pre"], _CLIP["tok"], _CLIP["torch"]


def embed_images(paths: list[Path], batch: int = 32) -> np.ndarray:
    from PIL import Image
    model, pre, _tok, torch = clip()
    out = []
    for i in range(0, len(paths), batch):
        x = torch.stack([pre(Image.open(p).convert("RGB")) for p in paths[i:i + batch]])
        with torch.no_grad():
            e = model.encode_image(x)
        out.append((e / e.norm(dim=-1, keepdim=True)).numpy())
    return np.concatenate(out) if out else np.zeros((0, 512), dtype="float32")


def embed_texts(texts: list[str]) -> np.ndarray:
    model, _pre, tok, torch = clip()
    with torch.no_grad():
        e = model.encode_text(tok(texts))
    return (e / e.norm(dim=-1, keepdim=True)).numpy()


def build(srcs: list[Path], out: Path, every: float = 3.0, trees: list[Path] | None = None, skip: tuple[str, ...] = (), keep_proxies: bool = False, movie_max: float = MOVIE_MAX_SEC) -> dict:
    for s in list(srcs) + list(trees or []):
        if not s.is_dir():
            raise PoolError(f"{s} is not a folder")
    out.mkdir(parents=True, exist_ok=True)
    frames = out / "frames"
    frames.mkdir(exist_ok=True)
    keep, left_out = list_sources(srcs, trees, skip, keep_proxies, movie_max)
    if not keep:
        raise PoolError("nothing usable in those folders (every file was left out)")
    rows = []
    for index, item in enumerate(keep):
        rows += extract_frames(item, frames, every, index)
        print(f"  {Path(item['file']).name}: {sum(1 for r in rows if r['file'] == item['file'])} frame(s)", flush=True)
    emb = embed_images([frames / r["frame"] for r in rows])
    np.save(out / "embeddings.npy", emb)
    meta = {"model": MODEL_ID, "every_sec": every, "sources": [str(s) for s in srcs], "trees": [str(t) for t in (trees or [])], "skipped_folders": list(skip), "frames": rows, "left_out": left_out,
            "files_used": len(keep), "frame_count": len(rows)}
    (out / "pool.json").write_text(json.dumps(meta, indent=1))
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", action="append", default=[], help="a folder of videos / images, read one level deep (repeat for more)")
    ap.add_argument("--tree", action="append", default=[], help="a folder walked all the way down, videos only (repeat for more)")
    ap.add_argument("--proxies", action="store_true", help="in a --tree read ONLY the `proxies` folders (small working copies of raw footage, named like the raw clips), not the originals beside them")
    ap.add_argument("--any-length", action="store_true", help="no 20-minute limit (raw camera clips run that long; the limit exists to catch full-length movies)")
    ap.add_argument("--skip-folder", action="append", default=[], help="leave out any folder in a --tree whose name contains this (repeat for more)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=float, default=3.0, help="seconds between frames (default 3)")
    a = ap.parse_args(argv)
    try:
        if not (a.src or a.tree):
            raise PoolError("give at least one --src or --tree folder")
        meta = build([Path(s).expanduser() for s in a.src], Path(a.out).expanduser(), a.every, [Path(t).expanduser() for t in a.tree], tuple(a.skip_folder), a.proxies, 0 if a.any_length else MOVIE_MAX_SEC)
    except PoolError as e:
        print(f"B-roll pool: {e}", file=sys.stderr)
        return 1
    print(f"pool: {meta['frame_count']} frames from {meta['files_used']} files; left out {len(meta['left_out'])}:")
    for lo in meta["left_out"]:
        print(f"  - {Path(lo['file']).name}: {lo['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
