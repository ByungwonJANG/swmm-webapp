"""Build scenario .inp files from a base SWMM model and run them with PySWMM.

All element names (rain gages, time series, subcatchments, links, nodes) and the
map coordinates are read from the .inp file, so any model can be used by changing
config.MODEL_PATH.
"""
import tempfile
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from pyswmm import Links, Nodes, Output, Simulation, SystemStats
from swmm.toolkit.shared_enum import LinkAttribute, NodeAttribute, SystemAttribute
from swmm_api import read_inp_file

from config import MODEL_PATH

# swmm-api tries to parse time series times with an en_US locale and warns when the
# locale is missing (e.g. on Linux servers). The values are still read as numbers and
# the times are written back unchanged, so the warning does not affect results.
warnings.filterwarnings("ignore", message=r"Could not convert Data for Timeseries", module=r"swmm_api")

US_FLOW_UNITS = {"CFS", "GPM", "MGD"}
FLOW_TO_CMS = {"CFS": 0.028316846592, "GPM": 6.30901964e-5, "MGD": 0.0438126364,
               "CMS": 1.0, "LPS": 0.001, "MLD": 0.0115740741}
FT3_TO_MGAL = 7.48052 / 1e6  # ft3 -> 10^6 gal (US report volume unit)
M3_TO_ML = 1e-3  # m3 -> 10^6 L (SI report volume unit)
MGAL_TO_M3 = 3785.411784
COLUMNS = ["category", "element", "metric", "value", "unit", "time_of_max(day hr:min)"]
LINK_SECTIONS = ("CONDUITS", "PUMPS", "ORIFICES", "WEIRS", "OUTLETS")
NODE_SECTIONS = {"JUNCTIONS": "JUNCTION", "OUTFALLS": "OUTFALL", "STORAGE": "STORAGE", "DIVIDERS": "DIVIDER"}


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
    node_types: dict[str, str] = field(default_factory=dict)  # name -> JUNCTION/OUTFALL/...
    node_xy: dict[str, tuple[float, float]] = field(default_factory=dict)
    link_paths: dict[str, list[tuple[float, float]]] = field(default_factory=dict)  # from node, vertices, to node
    polygons: dict[str, list[tuple[float, float]]] = field(default_factory=dict)  # subcatchment outlines

    @property
    def is_us(self):
        return self.flow_units in US_FLOW_UNITS


@dataclass
class RunResult:
    """summary: rows like parse_rpt.py, in model units (validated against runswmm.exe).
    series: report-step time series in SI units, one column per element:
      rainfall (mm/hr, area-averaged), outfall_flow (m3/s), node_depth (m),
      node_flooding (m3/s), link_flow (m3/s), link_fullness (% of full depth)."""
    summary: pd.DataFrame
    series: dict[str, pd.DataFrame]
    start: datetime
    report_step: int  # seconds


def _section(inp, name):
    """A parsed section, or {} when the model has none (inp.get returns unparsed text)."""
    return inp[name] if name in inp else {}


def model_info(path=MODEL_PATH):
    inp = read_inp_file(path)
    gage_series = [g.timeseries for g in inp.RAINGAGES.values() if g.source == "TIMESERIES"]
    timeseries = [name for name, ts in _section(inp, "TIMESERIES").items() if hasattr(ts, "data")]
    node_types = {name: kind for sec, kind in NODE_SECTIONS.items() for name in _section(inp, sec)}
    node_xy = {name: (c.x, c.y) for name, c in _section(inp, "COORDINATES").items()}
    vertices = _section(inp, "VERTICES")
    link_paths = {}
    for sec in LINK_SECTIONS:
        for name, link in _section(inp, sec).items():
            if link.from_node in node_xy and link.to_node in node_xy:
                mid = list(vertices[name].vertices) if name in vertices else []
                link_paths[name] = [node_xy[link.from_node], *mid, node_xy[link.to_node]]
    return ModelInfo(
        path=Path(path),
        flow_units=inp.OPTIONS["FLOW_UNITS"],
        timeseries=timeseries,
        default_timeseries=gage_series[0] if gage_series else None,
        subcatchments=len(_section(inp, "SUBCATCHMENTS")),
        nodes=len(node_types),
        conduits=len(_section(inp, "CONDUITS")),
        node_types=node_types,
        node_xy=node_xy,
        link_paths=link_paths,
        polygons={name: list(p.polygon) for name, p in _section(inp, "POLYGONS").items()},
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

    # The dashboard needs every node and link in the binary output.
    if "REPORT" in inp:
        inp.REPORT["NODES"] = "ALL"
        inp.REPORT["LINKS"] = "ALL"
    else:
        inp["REPORT"] = {"NODES": "ALL", "LINKS": "ALL"}

    inp.write_file(dst)


def _swmm_date(dt):
    delta = dt - datetime(1899, 12, 30)
    return delta.days + delta.seconds / 86400


def _elapsed(swmm_date, start):
    """Format a SWMM decimal date as 'days hr:min' elapsed since start, like the .rpt file."""
    secs = round((swmm_date - start) * 86400)
    days, secs = divmod(secs, 86400)
    return f"{days} {secs // 3600:02d}:{secs % 3600 // 60:02d}"


def _summarize(sim):
    """Summary rows (same items as parse_rpt.py) from a finished simulation."""
    rows = []
    add = lambda cat, elem, metric, val, unit, time="": rows.append([cat, elem, metric, val, unit, time])
    us = sim.flow_units in US_FLOW_UNITS
    depth_unit, rain_unit = ("ft", "in") if us else ("m", "mm")
    vol_unit, vol_factor = ("10^6 gal", FT3_TO_MGAL) if us else ("10^6 ltr", M3_TO_ML)
    start = _swmm_date(sim.start_time)

    stats = SystemStats(sim)
    runoff, routing = stats.runoff_stats, stats.routing_stats
    add("continuity", "runoff", "continuity_error", runoff["routing_error"], "%")
    add("continuity", "flow_routing", "continuity_error", routing["routing_error"], "%")
    add("system", "rainfall", "total_precipitation", runoff["rainfall"], rain_unit)
    add("system", "runoff", "total_surface_runoff", runoff["runoff"], rain_unit)

    nodes = list(Nodes(sim))
    for n in nodes:
        if n.is_outfall():
            add("outfall", n.nodeid, "peak_flow", n.outfall_statistics["peak_flowrate"], sim.flow_units,
                _elapsed(n.statistics["max_inflow_date"], start))

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

    return pd.DataFrame(rows, columns=COLUMNS), [n.nodeid for n in nodes if n.is_outfall()]


def _read_series(out_path, flow_units, outfalls):
    """Report-step time series from the binary output file, converted to SI units."""
    us = flow_units in US_FLOW_UNITS
    flow, depth, rain = FLOW_TO_CMS[flow_units], (0.3048 if us else 1.0), (25.4 if us else 1.0)

    with Output(str(out_path)) as out:
        times = pd.DatetimeIndex(out.times)

        def frame(getter, attribute, factor):
            rows = [getter(attribute, i) for i in range(len(times))]
            return pd.DataFrame(rows, index=times) * factor

        node_inflow = frame(out.node_attribute, NodeAttribute.TOTAL_INFLOW, flow)
        series = {
            "rainfall": pd.DataFrame({"rainfall": out.system_series(SystemAttribute.RAINFALL)}) * rain,
            "outfall_flow": node_inflow[[n for n in outfalls if n in node_inflow]],
            "node_depth": frame(out.node_attribute, NodeAttribute.INVERT_DEPTH, depth),
            "node_flooding": frame(out.node_attribute, NodeAttribute.FLOODING_LOSSES, flow),
            "link_flow": frame(out.link_attribute, LinkAttribute.FLOW_RATE, flow),
            "link_fullness": frame(out.link_attribute, LinkAttribute.CAPACITY, 100.0),
        }
        series["rainfall"].index = times
        return series, out.rpt_step


def simulate(inp_path):
    """Run an .inp with PySWMM; returns the summary and the report-step time series."""
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "run.out"
        with Simulation(str(inp_path), reportfile=str(Path(tmp) / "run.rpt"), outputfile=str(out_path)) as sim:
            for _ in sim:
                pass
            summary, outfalls = _summarize(sim)
            flow_units, start = sim.flow_units, sim.start_time
        series, report_step = _read_series(out_path, flow_units, outfalls)
    return RunResult(summary, series, start, report_step)


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


def headline(result):
    """Key figures for the result cards, in SI units."""
    summary = result.summary

    def one(category, metric):
        rows = summary[(summary.category == category) & (summary.metric == metric)]
        return rows.value.max(), rows.unit.iloc[0]

    flood = summary[summary.category == "node_flooding"]
    outfalls = summary[(summary.category == "outfall")]
    peak = outfalls.loc[outfalls.value.idxmax()]  # largest outfall peak
    fullness = result.series["link_fullness"].max()
    return {
        "total_precipitation": to_si(*one("system", "total_precipitation")),
        "total_runoff": to_si(*one("system", "total_surface_runoff")),
        "outfall_peak_flow": to_si(peak.value, peak.unit),
        "outfall_peak_time": peak["time_of_max(day hr:min)"],  # 'days hr:min' after start
        "outfall_peak_node": peak.element,
        "max_fullness": (fullness.max(), fullness.idxmax()) if len(fullness) else (0.0, ""),
        "flooded_nodes": int((flood.value > 0).sum()),
        "total_flood_volume": to_si(flood.value.sum(), flood.unit.iloc[0]) if len(flood) else (0.0, "m3"),
    }
