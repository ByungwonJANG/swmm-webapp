"""Compare runswmm.exe report summaries with PySWMM summaries.

Usage: python scripts/validate.py NAME [NAME ...]
Reads results/<NAME>_summary.csv (runswmm) and results/pyswmm_<NAME>_summary.csv,
writes results/validation.csv.

A value matches when the PySWMM value, rounded to the number of decimals printed
in the .rpt file, equals the .rpt value (i.e. |diff| <= half a unit of the last digit).
"""
import sys

import pandas as pd

KEY = ["category", "element", "metric"]
TIME = "time_of_max(day hr:min)"

frames = []
for name in sys.argv[1:]:
    rpt = pd.read_csv(f"results/{name}_summary.csv", dtype={"value": str})
    py = pd.read_csv(f"results/pyswmm_{name}_summary.csv")
    df = rpt.merge(py, on=KEY, suffixes=("_rpt", "_pyswmm"), validate="one_to_one")
    decimals = df["value_rpt"].str.split(".").str[1].str.len().fillna(0).astype(int)
    df["value_rpt"] = df["value_rpt"].astype(float)
    df["abs_diff"] = df["value_pyswmm"] - df["value_rpt"]
    df["pct_diff"] = (df["abs_diff"] / df["value_rpt"].abs() * 100).where(df["value_rpt"] != 0, 0.0)
    tol = 0.5 * 10.0 ** -decimals + 1e-9
    df["value_match"] = df["abs_diff"].abs() <= tol
    df["time_match"] = df[f"{TIME}_rpt"].fillna("") == df[f"{TIME}_pyswmm"].fillna("")
    df.insert(0, "scenario", name)
    frames.append(df[["scenario", *KEY, "unit_rpt", "value_rpt", "value_pyswmm", "abs_diff", "pct_diff",
                      "value_match", f"{TIME}_rpt", f"{TIME}_pyswmm", "time_match"]]
                  .rename(columns={"unit_rpt": "unit"}))

out = pd.concat(frames)
out.to_csv("results/validation.csv", index=False, float_format="%.6g")

summary = out.groupby(["scenario", "category"], sort=False).agg(
    items=("value_match", "size"),
    value_match=("value_match", "sum"),
    time_match=("time_match", "sum"),
    max_abs_pct_diff=("pct_diff", lambda s: s.abs().max()),
)
print(summary.to_string())
mismatch = out[~(out["value_match"] & out["time_match"])]
print(f"\nmismatches: {len(mismatch)}")
if len(mismatch):
    print(mismatch.to_string(index=False))
