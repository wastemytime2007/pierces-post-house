import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_reframe as tr


def test_an_info_row_is_printed_as_info_and_does_not_fail_the_run(tmp_path):
    """The lowered-clip row rests on an assumption and is only a report: the CLI once printed it as FAIL and exited 1, which stopped a whole reel build."""
    import copy
    src = tr._xml(tmp_path)
    plan = copy.deepcopy(tr.PLAN)
    plan["pieces"][1]["subject_y"] = 900
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    r = subprocess.run([sys.executable, str(HERE / "reframe_xml.py"), str(src), str(tmp_path / "plan.json"), "--out", str(tmp_path / "out.xml")], capture_output=True, text=True)
    assert "[INFO] info: VERTICAL-UNIT-ASSUMED" in r.stdout
    assert "[FAIL] info" not in r.stdout
