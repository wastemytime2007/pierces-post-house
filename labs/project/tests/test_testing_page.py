"""The testing page must never point at a file that is not there."""
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import testing_page as tp  # noqa: E402


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "Reviews"
    for _label, _look, links in [(t[1], t[2], t[3]) for t in tp.TESTS]:
        for _l, rel in links:
            (r / rel).parent.mkdir(parents=True, exist_ok=True)
            (r / rel).write_text("x")
    (r / "Runnells Tiling - project index.html").write_text("x")
    return r


def test_the_page_links_every_output_and_lists_the_decisions(root, monkeypatch):
    monkeypatch.setattr(tp, "ART", root / "Runnells Tiling - project index.html")            # the reference track lives outside the folder
    page, missing = tp.build(root)
    assert missing == [] and page.count("<tr>") == len(tp.TESTS)
    assert all(f'href="{quote(rel)}"' in page for t in tp.TESTS for _l, rel in t[3])
    assert page.count("<li>") == len(tp.DECISIONS) and "Integration into app/" in page and "stand-ins" in page


def test_a_missing_output_is_reported_and_nothing_is_written(root, monkeypatch, tmp_path):
    monkeypatch.setattr(tp, "ART", root / "Runnells Tiling - project index.html")
    (root / (tp.P + "QA pass (callout words)") / "qa_report.html").unlink()
    page, missing = tp.build(root)
    assert missing == [tp.P + "QA pass (callout words)/qa_report.html"]
    p = subprocess.run([sys.executable, str(HERE / "testing_page.py"), "--root", str(root)], capture_output=True, text=True)
    assert p.returncode == 1 and "points at files that are not there" in p.stderr and not (root / "TESTING - creator workflow.html").exists()
