"""App settings. To use another catchment model, change MODEL_PATH only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# SWMM input file the app runs scenarios on. It is never modified.
MODEL_PATH = ROOT / "models" / "base.inp"

# Scenario input ranges: (min, max, default, step)
RAIN_FACTOR = (1.0, 2.0, 1.0, 0.1)
IMPERV_DELTA = (-20.0, 30.0, 0.0, 5.0)  # %p
ROUGHNESS_FACTOR = (1.0, 1.5, 1.0, 0.05)
