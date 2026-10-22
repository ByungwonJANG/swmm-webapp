"""Build scenario .inp files from a base SWMM model and run them with PySWMM.

All element names (rain gages, time series, subcatchments, conduits, nodes) are
read from the .inp file, so any model can be used by changing config.MODEL_PATH.
"""
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from pyswmm import Links, Nodes, Simulation, SystemStats
from swmm_api import read_inp_file

from config import MODEL_PATH

US_FLOW_UNITS = {"CFS", "GPM", "MGD"}
FLOW_TO_CMS = {"CFS": 0.028316846592, "GPM": 6.30901964e-5, "MGD": 0.0438126364,
               "CMS": 1.0, "LPS": 0.001, "MLD": 0.0115740741}
FT3_TO_MGAL = 7.48052 / 1e6  # ft3 -> 10^6 gal (US report volume unit)
M3_TO_ML = 1e-3  # m3 -> 10^6 L (SI report volume unit)
MGAL_TO_M3 = 3785.411784
COLUMNS = ["category", "element", "metric", "value", "unit", "time_of_max(day hr:min)"]


@dataclass
class Scenario:
    timeseries: str | None = None  # None keeps the rain gages' own time series
    rain_factor: float = 1.0
    imperv_delta: float = 0.0  # percentage points added to every subcatchment
    roughness_factor: float = 1.0

    def is_default(self):
        return self.rain_factor == 1.0 and self.imperv_delta == 0.0 and self.roughness_factor == 1.0


@dataclass
class ModelInfo:
    path: Path
    flow_units: str
    timeseries: list[str]  # inline rainfall time series usable by the rain gages
    default_timeseries: str | None
    subcatchments: int
    nodes: int
    conduits: int

    @property
    def is_us(self):
        return self.flow_units in US_FLOW_UNITS


def _section(inp, name):
    """A parsed section, or {} when the model has none (inp.get returns unparsed text)."""
    return inp[name] if name in inp else {}


def model_info(path=MODEL_PATH):
    inp = read_inp_file(path)
    gage_series = [g.timeseries for g in inp.RAINGAGES.values() if g.source == "TIMESERIES"]
    timeseries = [name for name, ts in _section(inp, "TIMESERIES").items() if hasattr(ts, "data")]
    nodes = sum(len(_section(inp, s)) for s in ("JUNCTIONS", "OUTFALLS", "STORAGE", "DIVIDERS"))
    return ModelInfo(
        path=Path(path),
        flow_units=inp.OPTIONS["FLOW_UNITS"],
        timeseries=timeseries,
        default_timeseries=gage_series[0] if gage_series else None,
        subcatchments=len(_section(inp, "SUBCATCHMENTS")),
        nodes=nodes,
        conduits=len(_section(inp, "CONDUITS")),
    )


def build_inp(scenario, src, dst):
    """Write a copy of src with the scenario applied to dst. src is only read."""
    src = Path(src)
    inp = read_inp_file(src)

    gages = [g for g in inp.RAINGAGES.values() if g.source == "TIMESERIES"]
    if scenario.timeseries is not None:
        if scenario.timeseries not in inp.TIMESERIES:
            raise ValueError(f"time series not in model: {scenario.timeseries}")
        for g in gages:
            g.timeseries = scenario.timeseries
    if scenario.rain_factor != 1.0:
        for name in {g.timeseries for g in gages}:
            ts = inp.TIMESERIES[name]
            ts.data = [(*row[:-1], row[-1] * scenario.rain_factor) for row in ts.data]

    if scenario.imperv_delta != 0.0:
        for s in inp.SUBCATCHMENTS.values():
            s.imperviousness = min(100.0, max(0.0, s.imperviousness + scenario.imperv_delta))

    if scenario.roughness_factor != 1.0:
        for c in inp.CONDUITS.values():
            c.roughness *= scenario.roughness_factor

    # The copy lives in another folder, so external files must be referenced absolutely.
    for g in inp.RAINGAGES.values():
        if g.source == "FILE" and not Path(g.filename).is_absolute():
            g.filename = str((src.parent / g.filename).resolve())
    for ts in _section(inp, "TIMESERIES").values():
        if hasattr(ts, "filename") and not Path(ts.filename).is_absolute():
            ts.filename = str((src.parent / ts.filename).resolve())
    if "BACKDROP" in inp:
        del inp["BACKDROP"]

    inp.write_file(dst)


def _swmm_date(dt):
    delta = dt - datetime(1899, 12, 30)
    return delta.days + delta.seconds / 86400


def _elapsed(swmm_date, start):
    """Format a SWMM decimal date as 'days hr:min' elapsed since start, like the .rpt file."""
    secs = round((swmm_date - start) * 86400)
    days, secs = divmod(secs, 86400)
    return f"{days} {secs // 3600:02d}:{secs % 3600 // 60:02d}"


def simulate(inp_path):
    """Run an .inp with PySWMM and return the summary rows (same items as parse_rpt.py)."""
    rows = []
    add = lambda cat, elem, metric, val, unit, time="": rows.append([cat, elem, metric, val, unit, time])

    with tempfile.TemporaryDirectory() as tmp:
        sim = Simulation(str(inp_path), reportfile=str(Path(tmp) / "run.rpt"), outputfile=str(Path(tmp) / "run.out"))
        us = sim.flow_units in US_FLOW_UNITS
        depth_unit, rain_unit = ("ft", "in") if us else ("m", "mm")
        vol_unit, vol_factor = ("10^6 gal", FT3_TO_MGAL) if us else ("10^6 ltr", M3_TO_ML)
        start = _swmm_date(sim.start_time)
        for _ in sim:
            pass

        stats = SystemStats(sim)
        runoff, routing = stats.runoff_stats, stats.routing_stats
        add("continuity", "runoff", "continuity_error", runoff["routing_error"], "%")
        add("continuity", "flow_routing", "continuity_error", routing["routing_error"], "%")
        add("system", "rainfall", "total_precipitation", runoff["rainfall"], rain_unit)
        add("system", "runoff", "total_surface_runoff", runoff["runoff"], rain_unit)

        nodes = list(Nodes(sim))
        for n in nodes:
            if n.is_outfall():
                add("outfall", n.nodeid, "peak_flow", n.outfall_statistics["peak_flowrate"], sim.flow_units)

        for link in Links(sim):
            s = link.conduit_statistics
            add("link", link.linkid, "max_flow", abs(s["peak_flow"]), sim.flow_units, _elapsed(s["peak_flow_date"], start))

        for n in nodes:
            if n.is_outfall():
                continue
            s = n.statistics
            time = _elapsed(s["max_flooding_date"], start) if s["flooding_volume"] > 0 else ""
            add("node_flooding", n.nodeid, "total_flood_volume", s["flooding_volume"] * vol_factor, vol_unit, time)
        for n in nodes:
            s = n.statistics
            add("node_depth", n.nodeid, "max_depth", s["max_depth"], depth_unit, _elapsed(s["max_depth_date"], start))

        sim.close()

    return pd.DataFrame(rows, columns=COLUMNS)


def run_scenario(scenario, model_path=MODEL_PATH):
    """Apply the scenario to a temporary copy of the model and simulate it."""
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "scenario.inp"
        build_inp(scenario, model_path, inp)
        return simulate(inp)


def to_si(value, unit):
    """Convert a value in a model/report unit to SI (mm, m3/s, m, m3)."""
    factors = {"in": ("mm", 25.4), "mm": ("mm", 1.0), "ft": ("m", 0.3048), "m": ("m", 1.0),
               "10^6 gal": ("m3", MGAL_TO_M3), "10^6 ltr": ("m3", 1000.0)}
    factors.update({u: ("m3/s", f) for u, f in FLOW_TO_CMS.items()})
    si_unit, f = factors[unit]
    return value * f, si_unit


def headline(results):
    """Key figures for the result cards, in SI units."""
    def one(category, metric):
        rows = results[(results.category == category) & (results.metric == metric)]
        return rows.value.max(), rows.unit.iloc[0]

    flood = results[results.category == "node_flooding"]
    flooded = flood[flood.value > 0]
    return {
        "total_precipitation": to_si(*one("system", "total_precipitation")),
        "total_runoff": to_si(*one("system", "total_surface_runoff")),
        "outfall_peak_flow": to_si(*one("outfall", "peak_flow")),  # largest outfall peak
        "flooded_nodes": len(flooded),
        "total_flood_volume": to_si(flood.value.sum(), flood.unit.iloc[0]) if len(flood) else (0.0, "m3"),
    }
