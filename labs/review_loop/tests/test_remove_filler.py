"""'REMOVE THIS UM' (Ryan, 2026-10-10): the editor refused it with "No operation finds a filler word by listening". remove_filler finds the
filler word in the transcript near the note's moment and cuts its measured span."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ops as opsmod  # noqa: E402
import words as words_mod  # noqa: E402
from timeline import AudioClip, Cut, VideoClip  # noqa: E402


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_prompted_pass(monkeypatch):
    """The second, prompted transcription is off unless a test turns it on: it needs real media."""
    monkeypatch.setattr(words_mod, "words_prompted", lambda path, start, dur: [])


def _cut():
    cut = Cut("s", 30.0, 1080, 1920, 10.0)
    cut.video.append(VideoClip(1, 0.0, 10.0, "/a.mp4", 100.0, 110.0))
    cut.audio.append(AudioClip(0.0, 10.0, "/lav.wav", 100.0, 110.0, 1))
    return cut


def _fake(monkeypatch, rows):
    def fake(path, start, dur):
        return [words_mod.W(t, start + a, start + b) for t, a, b in rows]
    monkeypatch.setattr(words_mod, "words_in", fake)


def _note(text, at):
    return {"note": 1, "text": text, "timeline_sec": at, "clip": 1, "shapes": []}


def test_remove_this_um_cuts_the_measured_um(monkeypatch):
    _fake(monkeypatch, [("So", 0.5, 0.8), ("we", 0.8, 0.9), ("um", 1.2, 1.5), ("bought", 1.6, 2.0)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note('REMOVE THIS "UM"', 1.3)], _cut())
    assert out[0]["op"] == "remove_range"
    assert abs(out[0]["start"] - 1.2) < 0.05 and abs(out[0]["end"] - 1.5) < 0.05


def test_an_elongated_filler_is_cut_as_the_speaker_says_it(monkeypatch):
    """2026-10-11: the note "Remove this 'Umm'" was refused; the transcript has "umm", which the list did not know."""
    _fake(monkeypatch, [("So", 0.5, 0.8), ("umm,", 1.2, 1.6), ("bought", 1.7, 2.1)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("Remove this “Umm”", 1.3)], _cut())
    assert out[0]["op"] == "remove_range" and abs(out[0]["end"] - 1.6) < 0.05


def test_a_filler_word_far_from_the_note_is_not_cut(monkeypatch):
    _fake(monkeypatch, [("um", 8.0, 8.3)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("remove this um", 1.0)], _cut())
    assert out[0]["op"] == "unsupported" and "no filler word" in out[0]["reason"]


def _absolute_words(monkeypatch, rows):
    """Words at their source times (the cut's audio starts at source 100 s in these tests)."""
    def fake(path, start, dur):
        return [words_mod.W(t, a, b) for t, a, b in rows if a < start + dur and b > start]
    monkeypatch.setattr(words_mod, "words_in", fake)


def _burst_audio(monkeypatch, burst_at, burst_len, room_db=-60.0):
    """Room tone with one voiced burst (a 220 Hz tone) at `burst_at` seconds into the source, as ops._pcm would return it."""
    import numpy as np
    import ops
    sr = ops.SR

    def pcm(path, start, dur):
        n = int(dur * sr)
        t = start + np.arange(n) / sr
        x = np.random.default_rng(0).normal(0, 10 ** (room_db / 20), n).astype(np.float32)
        on = (t >= burst_at) & (t < burst_at + burst_len)
        x[on] = (0.2 * np.sin(2 * np.pi * 220 * t[on])).astype(np.float32)
        return x
    monkeypatch.setattr(ops, "_pcm", pcm)


def test_an_untranscribed_umm_is_cut_as_the_voiced_sound_when_the_transcript_has_nothing_there(monkeypatch):
    """2026-10-11: "Remove this Umm" — the transcript had no filler there, so the editor refused it. The voiced sound is measured and cut."""
    _absolute_words(monkeypatch, [("So", 100.2, 100.5), ("that", 102.2, 102.5)])     # real words elsewhere; the burst at 101.2 s (source) is not transcribed
    _burst_audio(monkeypatch, burst_at=101.2, burst_len=0.4)
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("Remove this Umm", 1.25)], _cut())
    assert out[0]["op"] == "remove_range", out
    assert 1.1 < out[0]["start"] < 1.25 and 1.5 < out[0]["end"] < 1.75


def _continuous_voice(monkeypatch):
    """Voiced sound all the way through, as ops._pcm returns it: no quiet gap and no burst of its own."""
    import numpy as np
    import ops
    sr = ops.SR

    def pcm(path, start, dur):
        n = int(dur * sr)
        t = start + np.arange(n) / sr
        return (0.2 * np.sin(2 * np.pi * 700.0 * t) + 0.05 * np.sin(2 * np.pi * 1400.0 * t)).astype(np.float32)
    monkeypatch.setattr(ops, "_pcm", pcm)


def test_a_filler_the_plain_transcript_misses_is_cut_where_the_prompted_pass_hears_it(monkeypatch):
    """2026-10-11, the Septic cut: "um," at 733.8-734.4 s was not in the plain transcript; the prompted pass writes it. The note at 6.65 s (src 734.0) is inside it."""
    _absolute_words(monkeypatch, [("for", 733.4, 734.9)])                       # the plain pass heard only real words around it
    _continuous_voice(monkeypatch)                                               # speech all through: no separate burst for the voiced-sound pass to find
    monkeypatch.setattr(words_mod, "words_prompted", lambda path, start, dur: [words_mod.W("um,", 101.2, 101.8)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("REMOVE THIS UM", 1.3)], _cut())
    assert out[0]["op"] == "remove_range", out
    # the word is 1.2-1.8 s; the span takes the sound whole: 0.3 s earlier at the onset, 0.15 s later at the tail
    assert abs(out[0]["start"] - 0.9) < 0.05 and abs(out[0]["end"] - 1.95) < 0.05


def test_a_prompted_filler_over_silence_is_refused(monkeypatch):
    """A prompted pass can write "um" where the audio is silent; that is not cut."""
    _absolute_words(monkeypatch, [])
    _burst_audio(monkeypatch, burst_at=200.0, burst_len=0.1)                     # nothing voiced near 101.2
    monkeypatch.setattr(words_mod, "words_prompted", lambda path, start, dur: [words_mod.W("um,", 101.2, 101.8)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("REMOVE THIS UM", 1.3)], _cut())
    assert out[0]["op"] == "unsupported"


def test_a_real_word_over_the_sound_is_never_cut_as_a_filler(monkeypatch):
    _absolute_words(monkeypatch, [("bought", 101.1, 101.6)])                   # the transcript shows a real word there
    _burst_audio(monkeypatch, burst_at=101.2, burst_len=0.4)
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("Remove this um", 1.25)], _cut())
    assert out[0]["op"] == "unsupported"


def test_a_note_that_does_not_ask_for_a_filler_is_refused(monkeypatch):
    _fake(monkeypatch, [("um", 1.2, 1.5)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("the picture is too dark", 1.3)], _cut())
    assert out[0]["op"] == "unsupported"
