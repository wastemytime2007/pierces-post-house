import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_site as cs
import pool as pl
import suggest as sg


def test_full_length_movies_and_film_releases_are_left_out_but_a_short_reel_is_kept():
    assert pl.classify(Path("Tomorrowland.2015.1080p.BluRay.x264.YIFY.mp4"), duration=60)[0] == "skip"          # named like a film release, whatever its length
    k, why = pl.classify(Path("The Fault in Our Stars.mp4"), duration=7974)
    assert k == "skip" and "movie" in why                                                                       # too long to be B-roll
    assert pl.classify(Path("DRONE PROS REEL.mp4"), duration=57)[0] == "video"
    assert pl.classify(Path("home.png"))[0] == "image"
    assert pl.classify(Path("Portfolio.prproj"))[0] == "skip" and pl.classify(Path("bg.psd"))[0] == "skip"
    assert pl.classify(Path("broken.mp4"), duration=0)[0] == "skip"                                             # unreadable duration is not silently kept


def test_listing_skips_hidden_files_subfolders_and_a_same_named_second_copy_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "probe_duration", lambda p: 30.0)
    for n in ("a.mp4", "a.mov", "b.png", ".hidden.mp4", "notes.txt", "Big.Film.2014.BluRay.x264.YIFY.mp4"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "_selects").mkdir()
    (tmp_path / "_selects" / "deep.mp4").write_bytes(b"x")
    keep, left = pl.list_sources([tmp_path])
    assert sorted(Path(k["file"]).name for k in keep) == ["a.mov", "b.png"]
    why = {Path(x["file"]).name: x["why"] for x in left}
    assert "a.mp4" in why and "same-named" in why["a.mp4"]          # the first of two same-named files (sorted) is kept, the other is reported
    assert "Big.Film.2014.BluRay.x264.YIFY.mp4" in why and "deep.mp4" not in why and "notes.txt" not in why


def test_menu_pages_takes_menu_words_on_the_same_site_once_each_and_ignores_other_links():
    page = ('<a href="/how-we-buy-houses/"><span>How It Works</span></a><a href="/faq">FAQ</a><a href="/faq/">FAQ</a>'
            '<a href="https://other.com/blog/">Blog</a><a href="/texas/">Texas</a><a href="#top">Press</a><a href="/our-company/">About Us \u203a</a>')
    pages = cs.menu_pages("https://soldfast.com", page.replace("\\u203a", "\u203a"), 10)
    assert [n for n, _u in pages] == ["home", "how_it_works", "faq", "about_us"]
    assert dict(pages)["faq"] == "https://soldfast.com/faq/"                                                      # trailing slash normalised, so the two FAQ links are one page
    assert len(cs.menu_pages("https://soldfast.com", page, 2)) == 2                                               # the limit holds


def test_load_lines_reads_caption_groups_and_plain_lists_and_refuses_an_empty_file(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"groups": [{"text": "hello there", "show_start": 1.0, "show_end": 2.5}, {"text": "", "show_start": 3, "show_end": 4}]}))
    assert sg.load_lines(f) == [{"text": "hello there", "start": 1.0, "end": 2.5}]
    f.write_text(json.dumps([{"text": "x", "start": 0, "end": 1}]))
    assert sg.load_lines(f)[0]["text"] == "x"
    f.write_text(json.dumps({"groups": []}))
    with pytest.raises(pl.PoolError):
        sg.load_lines(f)


def test_a_line_needs_both_a_good_score_and_to_stand_out_from_the_pool_and_one_clip_is_one_suggestion():
    assert sg.judge(0.33, 0.19) is True
    assert sg.judge(0.27, 0.16) is False                       # the nonsense matches on the real conversational lines
    assert sg.judge(0.33, 0.30) is False                       # scores high against everything: stands out from nothing
    emb = np.eye(4, dtype="float32")
    frames = [{"file": "a.mp4", "time": 0}, {"file": "a.mp4", "time": 3}, {"file": "b.mp4", "time": 0}, {"file": "c.png", "time": 0}]
    ranked, med = sg.rank(emb, np.array([1, 0.9, 0.5, 0], dtype="float32"), 3)
    assert [c["file"] for c in sg.pick(ranked, frames, 3)] == ["a.mp4", "b.mp4", "c.png"] and sg.pick(ranked, frames, 3)[0]["time"] == 0   # best frame per file, best file first


def test_every_image_on_the_contact_sheet_resolves_from_where_the_page_is_saved(tmp_path):
    import re
    pool_dir = tmp_path / "Reviews" / "pool"
    (pool_dir / "frames").mkdir(parents=True)
    (pool_dir / "frames" / "000_x_00001.jpg").write_bytes(b"jpg")
    out = tmp_path / "Reviews" / "sheets" / "one"
    row = {"text": "a line", "start": 0.0, "end": 1.0, "match": True, "median": 0.1,
           "candidates": [{"file": "/v/x.mp4", "time": 1.5, "frame": "000_x_00001.jpg", "kind": "video", "score": 0.4}]}
    page = sg.sheet([row], pool_dir, out, [])
    srcs = re.findall(r'<img src="([^"]+)"', page.read_text())
    assert srcs and all((page.parent / s).exists() for s in srcs)


def test_a_line_that_names_a_page_gets_that_page_and_the_most_specific_one():
    frames = [{"file": "/pool/soldfast.com captures/home.png", "kind": "image", "time": 0, "frame": "h.jpg"},
              {"file": "/pool/soldfast.com captures/reviews.png", "kind": "image", "time": 0, "frame": "r.jpg"},
              {"file": "/pool/soldfast.com captures/compare.png", "kind": "image", "time": 0, "frame": "c.jpg"},
              {"file": "/Desktop/Portfolio Videos/reviews.png", "kind": "image", "time": 0, "frame": "x.jpg"}]
    got = lambda t: [Path(f["file"]).name for f in sg.named_pages(t, frames)]
    assert got("Our reviews are on the SoldFast website") == ["reviews.png"]            # not the home page, though 'website' is there too
    assert got("Compare all three options") == ["compare.png"]
    assert got("Visit our website") == ["home.png"]
    assert got("I tore this piece off of here") == [] and got("Okay.") == []
    assert sg.named_pages("reviews", [frames[3]]) == []                                  # only a capture from a captures folder counts, not any file called reviews.png
