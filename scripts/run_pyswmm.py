"""Run SWMM models with PySWMM and write summaries in the same format as parse_rpt.py.

Usage: python scripts/run_pyswmm.py NAME=models/x.inp [NAME=models/y.inp ...]
Writes results/pyswmm_<NAME>_summary.csv for each model.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from model_runner import simulate  # noqa: E402


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        name, inp = arg.split("=", 1)
        csv_path = Path("results") / f"pyswmm_{name}_summary.csv"
        rows = simulate(Path(inp))
        rows.to_csv(csv_path, index=False)
        print(f"{inp}: {len(rows)} rows -> {csv_path}")
