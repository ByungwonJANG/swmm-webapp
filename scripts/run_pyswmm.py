"""Run SWMM models with PySWMM and write summaries in the same format as parse_rpt.py.

Usage: python scripts/run_pyswmm.py NAME=models/x.inp [NAME=models/y.inp ...]
Writes results/pyswmm_<NAME>_summary.csv for each model.
"""
import csv
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from pyswmm import Links, Nodes, Simulation, SystemStats

FT3_TO_MGAL = 7.48052 / 1e6  # ft3 -> 10^6 gal


def elapsed(swmm_date, start):
    """Format a SWMM decimal date as 'days hr:min' elapsed since start, like the .rpt file."""
    secs = round((swmm_date - start) * 86400)
    days, secs = divmod(secs, 86400)
    return f"{days} {secs // 3600:02d}:{secs % 3600 // 60:02d}"


def swmm_date(dt):
    """Convert a datetime to SWMM's decimal day number (days since 1899-12-30)."""
    delta = dt - datetime(1899, 12, 30)
    return delta.days + delta.seconds / 86400


def run(inp, csv_path):
    rows = []
    add = lambda cat, elem, metric, val, unit, time="": rows.append([cat, elem, metric, val, unit, time])

    with tempfile.TemporaryDirectory() as tmp:
        sim = Simulation(str(inp), reportfile=str(Path(tmp) / "run.rpt"), outputfile=str(Path(tmp) / "run.out"))
        start = swmm_date(sim.start_time)
        for _ in sim:
            pass

        stats = SystemStats(sim)
        runoff, routing = stats.runoff_stats, stats.routing_stats
        add("continuity", "runoff", "continuity_error", runoff["routing_error"], "%")
        add("continuity", "flow_routing", "continuity_error", routing["routing_error"], "%")
        add("system", "rainfall", "total_precipitation", runoff["rainfall"], "in")
        add("system", "runoff", "total_surface_runoff", runoff["runoff"], "in")

        nodes = list(Nodes(sim))
        for n in nodes:
            if n.is_outfall():
                add("outfall", n.nodeid, "peak_flow", n.outfall_statistics["peak_flowrate"], "CFS")

        for link in Links(sim):
            s = link.conduit_statistics
            add("link", link.linkid, "max_flow", abs(s["peak_flow"]), "CFS", elapsed(s["peak_flow_date"], start))

        for n in nodes:
            if n.is_outfall():
                continue
            s = n.statistics
            time = elapsed(s["max_flooding_date"], start) if s["flooding_volume"] > 0 else ""
            add("node_flooding", n.nodeid, "total_flood_volume", s["flooding_volume"] * FT3_TO_MGAL, "10^6 gal", time)
        for n in nodes:
            s = n.statistics
            add("node_depth", n.nodeid, "max_depth", s["max_depth"], "ft", elapsed(s["max_depth_date"], start))

        sim.close()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category", "element", "metric", "value", "unit", "time_of_max(day hr:min)"])
        w.writerows(rows)
    print(f"{inp}: {len(rows)} rows -> {csv_path}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        name, inp = arg.split("=", 1)
        run(Path(inp), Path("results") / f"pyswmm_{name}_summary.csv")
