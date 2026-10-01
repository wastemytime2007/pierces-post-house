import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_site as cs
import judge as jg
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


def test_a_tree_is_walked_for_videos_only_and_named_and_cache_folders_are_left_out_and_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "probe_duration", lambda p: 30.0)
    top = tmp_path / "SOLDFAST"
    for rel in ("REELS/a.mp4", "REELS/qr.png", "Testimony Videos/t.mp4", "TRAININGS 6:25/long.mp4", "SoldFast 2026/Adobe Premiere Pro Auto-Save/x.mp4",
                "SoldFast 2026/deep/b.mov", "SoldFast 2026/proxies/p.mp4", "House Reel.mp4", "layers.psd"):
        f = top / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    keep, left = pl.list_sources([], [top], ("Testimony", "TRAININGS"))
    assert sorted(str(Path(k["file"]).relative_to(top)) for k in keep) == ["House Reel.mp4", "REELS/a.mp4", "SoldFast 2026/deep/b.mov"]
    assert all(k["kind"] == "video" for k in keep)                                             # the png and psd are not B-roll in a tree
    skipped = sorted(Path(x["file"]).name for x in left if "skipped on purpose" in x["why"])
    assert skipped == ["TRAININGS 6:25", "Testimony Videos"]                                    # said, not silently dropped
    keep2, _ = pl.list_sources([], [top], ())
    assert any("Testimony" in k["file"] for k in keep2)                                         # without --skip-folder it is read


def test_excluded_files_never_appear_among_a_lines_candidates(tmp_path, monkeypatch):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    frames = [{"file": "/x/How To - Tile A Bathroom.mp4", "time": 0, "frame": "a.jpg", "kind": "video"}, {"file": "/x/Other.mp4", "time": 0, "frame": "b.jpg", "kind": "video"}]
    (pool_dir / "pool.json").write_text(json.dumps({"frames": frames}))
    np.save(pool_dir / "embeddings.npy", np.eye(2, dtype="float32"))
    monkeypatch.setattr(sg.pl, "embed_texts", lambda texts: np.array([[1.0, 0.0]] * len(texts), dtype="float32"))        # the line looks exactly like the first frame
    row = sg.suggest(pool_dir, [{"text": "tile a bathroom", "start": 0, "end": 1}], 3, exclude=("Tile A Bathroom",))[0]
    assert [Path(c["file"]).name for c in row["candidates"]] == ["Other.mp4"]
    row = sg.suggest(pool_dir, [{"text": "tile a bathroom", "start": 0, "end": 1}], 3)[0]
    assert Path(row["candidates"][0]["file"]).name == "How To - Tile A Bathroom.mp4"            # and without it, the cut finds itself


def test_proxies_mode_takes_only_the_proxy_copies_and_never_the_originals_beside_them(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "probe_duration", lambda p: 30.0)
    top = tmp_path / "Shoot"
    for rel in ("Day 1/Osmo/DJI_0001_D.MP4", "Day 1/Osmo/proxies/DJI_0001_D.mp4", "Day 1/Osmo/proxies/._DJI_0001_D.mp4", "Day 2/Osmo/DJI_0002_D.MP4"):
        f = top / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    keep, _ = pl.list_sources([], [top], (), keep_proxies=True)
    assert [str(Path(k["file"]).relative_to(top)) for k in keep] == ["Day 1/Osmo/proxies/DJI_0001_D.mp4"]          # not the originals, not the ._ AppleDouble file
    keep_default, _ = pl.list_sources([], [top], ())
    assert sorted(Path(k["file"]).name for k in keep_default) == ["DJI_0001_D.MP4", "DJI_0002_D.MP4"]            # by default proxies are cache and skipped


def test_a_missing_short_or_garbled_vision_reply_is_a_no_for_that_frame_never_a_yes():
    ok = '[{"n":1,"fits":true,"text_overlay":false,"why":"valve under sink"},{"n":2,"fits":false,"text_overlay":true,"why":"caption"}]'
    v = jg.parse("Here you go:\n" + ok + "\nDone.", 2)
    assert [(x["fits"], x["text_overlay"]) for x in v] == [(True, False), (False, True)]
    short = jg.parse(ok, 3)
    assert short[2] == {"fits": False, "text_overlay": False, "why": "no verdict returned"}          # the model skipped frame 3
    assert all(not x["fits"] for x in jg.parse("I could not read the images", 2)) and all(not x["fits"] for x in jg.parse("[not json]", 2))
    assert jg.parse('[{"n":1,"fits":"yes"}]', 1)[0]["fits"] is False                                   # only a real true counts
    assert jg.parse('[{"n":9,"fits":true}]', 2)[0]["fits"] is False                                    # an out-of-range number is ignored


def test_judge_line_caches_by_line_and_frames_and_refuses_frames_that_are_not_there(tmp_path):
    f1, f2 = tmp_path / "a.jpg", tmp_path / "b.jpg"
    f1.write_bytes(b"x")
    f2.write_bytes(b"x")
    calls = []
    ask = lambda prompt: (calls.append(prompt), '[{"n":1,"fits":true,"text_overlay":false,"why":"ok"},{"n":2,"fits":false,"text_overlay":false,"why":"no"}]')[1]
    cache = {}
    a = jg.judge_line("a valve", [f1, f2], ask, cache)
    b = jg.judge_line("a valve", [f1, f2], ask, cache)
    assert a == b and len(calls) == 1                                                                   # the second call is free
    jg.judge_line("another line", [f1, f2], ask, cache)
    assert len(calls) == 2                                                                              # a different line is a different question
    assert "a valve" in calls[0] and str(f1) in calls[0] and "text_overlay" in calls[0]
    with pytest.raises(jg.JudgeError):
        jg.judge_line("x", [tmp_path / "missing.jpg"], ask, {})
    assert jg.judge_line("x", [], ask, {}) == []


def test_with_vision_a_line_matches_only_when_a_frame_fits_and_has_no_text_and_a_failed_check_is_none_and_says_so(tmp_path, monkeypatch):
    pool_dir = tmp_path / "pool"
    (pool_dir / "frames").mkdir(parents=True)
    names = ["valve", "caption", "wrong"]
    frames = []
    for i, n in enumerate(names):
        (pool_dir / "frames" / f"{n}.jpg").write_bytes(b"x")
        frames.append({"file": f"/x/{n}.mp4", "time": 1.5, "frame": f"{n}.jpg", "kind": "video"})
    (pool_dir / "pool.json").write_text(json.dumps({"frames": frames}))
    np.save(pool_dir / "embeddings.npy", np.array([[1, 0, 0], [0.9, 0.1, 0], [0.8, 0.2, 0]], dtype="float32"))
    monkeypatch.setattr(sg.pl, "embed_texts", lambda texts: np.array([[1.0, 0.0, 0.0]] * len(texts), dtype="float32"))
    reply = '[{"n":1,"fits":true,"text_overlay":false,"why":"valve"},{"n":2,"fits":true,"text_overlay":true,"why":"caption on it"},{"n":3,"fits":false,"text_overlay":false,"why":"unrelated"}]'
    row = sg.suggest(pool_dir, [{"text": "the shut-off valve", "start": 0, "end": 1}], vision=True, ask=lambda p: reply, vision_floor=0.0)[0]
    assert row["match"] and row["how"] == "confirmed by vision"
    assert [Path(c["file"]).name for c in row["candidates"]] == ["valve.mp4"]                          # the captioned frame "fits" but is refused
    none = '[{"n":1,"fits":false,"text_overlay":false,"why":"x"},{"n":2,"fits":false,"text_overlay":false,"why":"y"},{"n":3,"fits":false,"text_overlay":false,"why":"z"}]'
    row = sg.suggest(pool_dir, [{"text": "it is always on the left", "start": 0, "end": 1}], vision=True, ask=lambda p: none, vision_floor=0.0)[0]
    assert not row["match"] and row["how"] == "none" and len(row["candidates"]) == 3 and row["candidates"][0]["verdict"]["why"] == "x"   # rejected ones are shown with the reason

    def boom(prompt):
        raise jg.JudgeError("claude CLI exited 1: not logged in")
    row = sg.suggest(pool_dir, [{"text": "the shut-off valve", "start": 0, "end": 1}], vision=True, ask=boom, vision_floor=0.0)[0]
    assert not row["match"] and "not logged in" in row["vision_error"]                                  # an error is never read as a yes
    row = sg.suggest(pool_dir, [{"text": "the shut-off valve", "start": 0, "end": 1}], vision=True, ask=lambda p: reply, vision_floor=1.5)[0]
    assert not row["match"] and row["checked"] == 0                                                      # below the floor: no model call at all


def test_any_length_keeps_long_raw_clips_but_a_film_release_name_is_still_left_out():
    assert pl.classify(Path("DJI_0001_D.mp4"), duration=1800)[0] == "skip"                                       # default: 30 minutes reads as a movie
    assert pl.classify(Path("DJI_0001_D.mp4"), duration=1800, movie_max=0)[0] == "video"                         # --any-length: a raw clip
    assert pl.classify(Path("Big.Film.2014.BluRay.x264.YIFY.mp4"), duration=60, movie_max=0)[0] == "skip"       # a film release is not footage Ryan shot, whatever its length
