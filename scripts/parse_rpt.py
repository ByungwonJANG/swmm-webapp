import csv
import re
import sys

rpt_path, csv_path = sys.argv[1], sys.argv[2]
lines = open(rpt_path, encoding="utf-8", errors="replace").read().splitlines()


def section(title):
    """Return the lines following a '***' boxed section title, up to the next boxed title."""
    for i, line in enumerate(lines):
        if line.strip().startswith(title) and i > 0 and lines[i - 1].strip().startswith("***"):
            out = []
            for l in lines[i + 2:]:
                if l.strip().startswith("***"):
                    break
                out.append(l)
            return out
    raise ValueError(f"section not found: {title}")


def value(sec, label, col=0):
    for l in sec:
        if l.strip().startswith(label):
            return re.findall(r"-?\d+\.\d+", l)[col]
    raise ValueError(f"label not found: {label}")


def table(sec):
    """Data rows of a dashed table (rows after the second dashed line)."""
    dashes = [i for i, l in enumerate(sec) if l.strip().startswith("---")]
    rows = []
    for l in sec[dashes[1] + 1:]:
        if not l.strip() or l.strip().startswith("---"):
            break
        rows.append(l.split())
    return rows


rows = []
add = lambda cat, elem, metric, val, unit, time="": rows.append([cat, elem, metric, val, unit, time])

runoff = section("Runoff Quantity Continuity")
routing = section("Flow Routing Continuity")
add("continuity", "runoff", "continuity_error", value(runoff, "Continuity Error"), "%")
add("continuity", "flow_routing", "continuity_error", value(routing, "Continuity Error"), "%")

add("system", "rainfall", "total_precipitation", value(runoff, "Total Precipitation", 1), "in")
add("system", "rainfall", "total_precipitation_volume", value(runoff, "Total Precipitation", 0), "ac-ft")
add("system", "runoff", "total_surface_runoff", value(runoff, "Surface Runoff", 1), "in")
add("system", "runoff", "total_surface_runoff_volume", value(runoff, "Surface Runoff", 0), "ac-ft")

inflow_time = {r[0]: f"{r[4]} {r[5]}" for r in table(section("Node Inflow Summary"))}
for r in table(section("Outfall Loading Summary")):
    if r[0] == "System":
        continue
    add("outfall", r[0], "peak_flow", r[3], "CFS", inflow_time[r[0]])
    add("outfall", r[0], "total_volume", r[4], "10^6 gal")

for r in table(section("Link Flow Summary")):
    add("link", r[0], "max_flow", r[2], "CFS", f"{r[3]} {r[4]}")

nodes = [r for r in table(section("Node Depth Summary"))]
flood = section("Node Flooding Summary")
flooded = {}
if not any("No nodes were flooded" in l for l in flood):
    # Node, Hours Flooded, Max Rate CFS, day, hr:min, Total Flood Volume 10^6 gal, Max Ponded Depth
    for r in table(flood):
        flooded[r[0]] = (r[2], f"{r[3]} {r[4]}", r[5])
for r in nodes:
    if r[1] == "OUTFALL":
        continue
    rate, time, vol = flooded.get(r[0], ("0.00", "", "0.000"))
    add("node_flooding", r[0], "total_flood_volume", vol, "10^6 gal", time)
for r in nodes:
    add("node_depth", r[0], "max_depth", r[3], "ft", f"{r[5]} {r[6]}")

with open(csv_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["category", "element", "metric", "value", "unit", "time_of_max(day hr:min)"])
    w.writerows(rows)
print(f"{len(rows)} rows -> {csv_path}")
