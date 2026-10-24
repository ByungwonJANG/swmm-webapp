"""Plotly figures for the result dashboard. Inputs are RunResult series (SI units)."""
import numpy as np
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots

FULLNESS_SCALE = "RdYlBu_r"  # blue (empty) -> red (full)
FULLNESS_BINS = 10


def hydrograph(rain, outfall_flow):
    """Rain bars hanging from the top over outfall hydrographs, on one time axis."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.3, 0.7], vertical_spacing=0.04)
    fig.add_bar(x=rain.index, y=rain["rainfall"], name="강우강도", marker_color="#4a90d9",
                hovertemplate="%{x|%H:%M}<br>%{y:.1f} mm/hr<extra></extra>", row=1, col=1)
    for name in outfall_flow.columns:
        fig.add_scatter(x=outfall_flow.index, y=outfall_flow[name], name=f"방류구 {name}", mode="lines",
                        hovertemplate="%{x|%H:%M}<br>%{y:.3f} ㎥/s<extra>" + name + "</extra>", row=2, col=1)
    fig.update_yaxes(title_text="강우강도 (mm/hr)", autorange="reversed", row=1, col=1)
    fig.update_yaxes(title_text="유량 (㎥/s)", rangemode="tozero", row=2, col=1)
    fig.update_xaxes(title_text="시각", tickformat="%H:%M", row=2, col=1)
    fig.update_layout(height=520, bargap=0, hovermode="x unified", margin=dict(t=30, b=40, l=60, r=20),
                      legend=dict(orientation="h", y=1.08))
    return fig


def network_map(info, link_value, node_size_value, node_hover, link_hover, size_label):
    """Network drawn from .inp coordinates.

    link_value: fullness (%) per link, colors the links blue -> red.
    node_size_value: value per node (>= 0) the circle area is proportional to.
    node_hover / link_hover: extra hover text per element.
    """
    fig = go.Figure()

    for name, poly in info.polygons.items():
        xs, ys = zip(*poly)
        fig.add_scatter(x=[*xs, xs[0]], y=[*ys, ys[0]], mode="lines", fill="toself", hoverinfo="skip",
                        line=dict(color="rgba(150,150,150,0.4)", width=1), fillcolor="rgba(150,150,150,0.08)",
                        showlegend=False)

    # Links: one trace per fullness bin (a line trace has a single color).
    colors = sample_colorscale(FULLNESS_SCALE, [(i + 0.5) / FULLNESS_BINS for i in range(FULLNESS_BINS)])
    bins = {}
    for name, path in info.link_paths.items():
        v = link_value.get(name, np.nan)
        b = FULLNESS_BINS - 1 if np.isnan(v) else min(int(v / 100 * FULLNESS_BINS), FULLNESS_BINS - 1)
        bins.setdefault(b, []).append(path)
    for b, paths in sorted(bins.items()):
        xs, ys = [], []
        for path in paths:
            px, py = zip(*path)
            xs += [*px, None]
            ys += [*py, None]
        fig.add_scatter(x=xs, y=ys, mode="lines", line=dict(color=colors[b], width=4), hoverinfo="skip",
                        showlegend=False)

    # Hover points at link midpoints, also carrying the fullness colorbar.
    names = list(info.link_paths)
    mids = [_midpoint(info.link_paths[n]) for n in names]
    fig.add_scatter(
        x=[m[0] for m in mids], y=[m[1] for m in mids], mode="markers", name="관로",
        marker=dict(size=7, color=[link_value.get(n, np.nan) for n in names], colorscale=FULLNESS_SCALE,
                    cmin=0, cmax=100, colorbar=dict(title="충만도 (%)", thickness=12), symbol="diamond"),
        text=[f"<b>관로 {n}</b><br>{link_hover.get(n, '')}" for n in names],
        hovertemplate="%{text}<extra></extra>", showlegend=False)

    nodes = [n for n in info.node_types if n in info.node_xy]
    values = np.array([max(node_size_value.get(n, 0.0), 0.0) for n in nodes])
    vmax = values.max() if len(values) and values.max() > 0 else 1.0
    sizes = 8 + 42 * np.sqrt(values / vmax)  # circle area proportional to the value
    fig.add_scatter(
        x=[info.node_xy[n][0] for n in nodes], y=[info.node_xy[n][1] for n in nodes], mode="markers",
        name=size_label,
        marker=dict(size=sizes, sizemode="diameter",
                    color=["#d62728" if v > 0 else ("#2ca02c" if info.node_types[n] == "OUTFALL" else "#555")
                           for n, v in zip(nodes, values)],
                    opacity=0.75, line=dict(color="white", width=1)),
        text=[f"<b>{_node_label(info.node_types[n])} {n}</b><br>{node_hover.get(n, '')}" for n in nodes],
        customdata=nodes, hovertemplate="%{text}<extra></extra>", showlegend=False)

    fig.update_yaxes(scaleanchor="x", scaleratio=1, visible=False)
    fig.update_xaxes(visible=False)
    fig.update_layout(height=620, margin=dict(t=10, b=10, l=10, r=10), plot_bgcolor="white", hovermode="closest")
    return fig


def element_series(frames):
    """Stacked time series, one row per (title, unit, series)."""
    fig = make_subplots(rows=len(frames), cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=[f"{t} ({u})" for t, u, _ in frames])
    for i, (title, unit, s) in enumerate(frames, start=1):
        fig.add_scatter(x=s.index, y=s.values, mode="lines", name=title,
                        hovertemplate="%{x|%H:%M}<br>%{y:.3f} " + unit + "<extra></extra>", row=i, col=1)
        fig.update_yaxes(rangemode="tozero", row=i, col=1)
    fig.update_xaxes(tickformat="%H:%M", row=len(frames), col=1)
    fig.update_layout(height=220 * len(frames) + 60, showlegend=False, hovermode="x unified",
                      margin=dict(t=40, b=40, l=60, r=20))
    return fig


def _midpoint(path):
    """Point halfway along a polyline."""
    pts = np.array(path, dtype=float)
    seg = np.hypot(*np.diff(pts, axis=0).T)
    if seg.sum() == 0:
        return tuple(pts[0])
    half, acc = seg.sum() / 2, np.concatenate([[0], np.cumsum(seg)])
    i = min(np.searchsorted(acc, half) - 1, len(seg) - 1)
    t = (half - acc[i]) / seg[i] if seg[i] else 0
    return tuple(pts[i] + t * (pts[i + 1] - pts[i]))


def _node_label(kind):
    return {"JUNCTION": "노드", "OUTFALL": "방류구", "STORAGE": "저류지", "DIVIDER": "분배점"}.get(kind, "노드")
