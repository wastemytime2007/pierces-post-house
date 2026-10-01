"""Tests here that run the bleep tool use an empty learning folder too, so a model learned from Ryan's real edits can never change what a test expects (a learned 0.035 s padding broke one on 2026-09-30)."""
import pytest


@pytest.fixture(autouse=True)
def _no_learned_model(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(tmp_path / "no_learning_yet"))
