"""Tests for app/model_runner.py. Run: python tests/test_model_runner.py"""
import hashlib
import sys
import tempfile
from pathlib import Path

import pandas as pd
from swmm_api import read_inp_file

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from config import MODEL_PATH  # noqa: E402
from model_runner import Scenario, build_inp, model_info, run_scenario, simulate  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_default_scenario_matches_base():
    before = sha(MODEL_PATH)
    info = model_info()
    scenario = Scenario(timeseries=info.default_timeseries)
    assert scenario.is_default()

    result = run_scenario(scenario)
    base = simulate(MODEL_PATH)
    pd.testing.assert_frame_equal(result, base)  # exact equality, no tolerance

    # The validated runswmm.exe summary of base.inp, rounded as the .rpt prints it.
    rpt = pd.read_csv(ROOT / "results" / "base_summary.csv", dtype={"value": str})
    merged = rpt.merge(result, on=["category", "element", "metric"], suffixes=("_rpt", ""))
    decimals = merged.value_rpt.str.split(".").str[1].str.len()
    diff = (merged.value - merged.value_rpt.astype(float)).abs()
    assert (diff <= 0.5 * 10.0 ** -decimals + 1e-9).all()
    assert len(merged) == len(result)

    assert sha(MODEL_PATH) == before, "base model was modified"


def test_scenario_edits():
    info = model_info()
    other = [t for t in info.timeseries if t != info.default_timeseries][-1]
    scenario = Scenario(timeseries=other, rain_factor=1.5, imperv_delta=30, roughness_factor=1.3)
    base = read_inp_file(MODEL_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.inp"
        build_inp(scenario, MODEL_PATH, out)
        new = read_inp_file(out)

    for g in new.RAINGAGES.values():
        assert g.timeseries == other
    for old_row, new_row in zip(base.TIMESERIES[other].data, new.TIMESERIES[other].data):
        assert abs(new_row[-1] - old_row[-1] * 1.5) < 1e-9
    for name, s in new.SUBCATCHMENTS.items():
        assert s.imperviousness == min(100.0, base.SUBCATCHMENTS[name].imperviousness + 30)
        assert 0 <= s.imperviousness <= 100
    for name, c in new.CONDUITS.items():
        assert abs(c.roughness - base.CONDUITS[name].roughness * 1.3) < 1e-12

    lower = Scenario(imperv_delta=-20)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.inp"
        build_inp(lower, MODEL_PATH, out)
        assert min(s.imperviousness for s in read_inp_file(out).SUBCATCHMENTS.values()) == 0.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
