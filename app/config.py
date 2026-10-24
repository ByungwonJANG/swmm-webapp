"""App settings. To use another catchment model, change MODEL_PATH only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# SWMM input file the app runs scenarios on. It is never modified.
MODEL_PATH = ROOT / "models" / "base.inp"

# Scenario input ranges: (min, max, default, step)
RAIN_FACTOR = (1.0, 2.0, 1.0, 0.1)
IMPERV_DELTA = (-20.0, 30.0, 0.0, 5.0)  # %p
ROUGHNESS_FACTOR = (1.0, 1.5, 1.0, 0.05)

# Saved scenarios kept for comparison.
MAX_SAVED_SCENARIOS = 5

# "기본 시나리오 일괄 실행": (name, time series, rain factor, imperviousness change %p, roughness factor).
# The time series names must exist in MODEL_PATH; change them together with the model.
BATCH_SCENARIOS = [
    ("2yr 원설계", "2-yr", 1.0, 0.0, 1.0),
    ("10yr 원설계", "10-yr", 1.0, 0.0, 1.0),
    ("100yr 원설계", "100-yr", 1.0, 0.0, 1.0),
    ("100yr 기후변화 ×1.3", "100-yr", 1.3, 0.0, 1.0),
    ("100yr ×1.3 · 불투수 +20%p · 조도 ×1.3", "100-yr", 1.3, 20.0, 1.3),
]
