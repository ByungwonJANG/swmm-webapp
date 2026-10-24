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
from model_runner import Scenario, build_inp, kpis, model_info, run_scenario, simulate  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_default_scenario_matches_base():
    before = sha(MODEL_PATH)
    info = model_info()
    scenario = Scenario(timeseries=info.default_timeseries)
    assert scenario.is_default()

    run = run_scenario(scenario)
    base = simulate(MODEL_PATH)
    pd.testing.assert_frame_equal(run.summary, base.summary)  # exact equality, no tolerance
    for key, frame in base.series.items():
        pd.testing.assert_frame_equal(run.series[key], frame)
    result = run.summary

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


def test_series_match_summary():
    run = simulate(MODEL_PATH)
    s = run.series
    info = model_info()
    assert set(s["node_depth"].columns) == set(info.node_types)
    assert set(s["link_flow"].columns) == set(info.link_paths)
    assert list(s["outfall_flow"].columns) == [n for n, t in info.node_types.items() if t == "OUTFALL"]
    assert len({len(f) for f in s.values()}) == 1  # one shared time axis
    # Report-step peaks can only be at or below the routing-step peaks of the summary.
    peak = run.summary.query("category == 'outfall'").value.max() * 0.028316846592
    assert 0.98 * peak <= s["outfall_flow"].max().max() <= peak + 1e-9
    # Rainfall depth from the intensity series equals the summary total (mm).
    depth = s["rainfall"]["rainfall"].sum() * run.report_step / 3600
    total = run.summary.query("metric == 'total_precipitation'").value.iloc[0] * 25.4
    assert abs(depth - total) < 0.01 * total


def test_locale_failure_does_not_change_results():
    """swmm-api warns 'Could not convert Data' when the en_US locale is missing; results must not change."""
    import locale
    real = locale.setlocale

    def no_en_us(category, loc=None):
        if loc is not None and "en_US" in str(loc):
            raise locale.Error("unsupported locale setting")
        return real(category) if loc is None else real(category, loc)

    scenario = Scenario("100-yr", 1.5, 20, 1.3)
    expected = run_scenario(scenario).summary
    locale.setlocale = no_en_us
    try:
        assert isinstance(read_inp_file(MODEL_PATH).TIMESERIES["2-yr"].data[0][0], str)  # conversion did fail
        pd.testing.assert_frame_equal(run_scenario(scenario).summary, expected)
    finally:
        locale.setlocale = real


def test_label_and_kpis():
    assert Scenario("100-yr", 1.5, 20, 1.3).label() == "100yr×1.5/+20%p/n×1.3"
    assert Scenario("2-yr").label() == "2yr×1/+0%p/n×1"
    k = kpis(simulate(MODEL_PATH))
    assert k["outfall_peak_time"] == 36 and k["flooded_nodes"] == 0 and k["total_flood_volume"] == 0
    assert abs(k["outfall_peak_flow"] - 33.44 * 0.028316846592) < 0.001


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
