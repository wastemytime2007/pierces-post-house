"""Every bleep test runs with an empty learning folder, so a model learned from Ryan's real edits can never change what a test expects."""
import pytest


@pytest.fixture(autouse=True)
def _no_learned_model(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTHOUSE_BLEEP_LEARNING", str(tmp_path / "no_learning_yet"))
