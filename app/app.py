"""Streamlit UI: set scenario conditions, run the SWMM model and explore the results.

Run: streamlit run app/app.py
"""
from dataclasses import asdict

import pandas as pd
import streamlit as st

import charts
import config
from model_runner import Scenario, headline, model_info, run_scenario, to_si

st.set_page_config(page_title="SWMM 배수 시나리오 분석", layout="wide")
st.title("SWMM 기반 도시 배수 시나리오 분석")


@st.cache_data
def load_info(path):
    return model_info(path)


@st.cache_data(show_spinner=False)
def run_cached(scenario, path):
    return run_scenario(scenario, path)


def fmt_elapsed(text):
    """'0 00:36' (days hr:min after start) -> '00:36', or '1일 02:10' after the first day."""
    days, hm = text.split()
    return hm if days == "0" else f"{days}일 {hm}"


info = load_info(config.MODEL_PATH)
unit_note = f"{info.flow_units}, 미국 단위 → SI 단위로 환산해 표시" if info.is_us else f"{info.flow_units}, SI 단위"
st.caption(f"모델: `{info.path.name}` · 소유역 {info.subcatchments}개 · 노드 {info.nodes}개 · "
           f"관로 {info.conduits}개 · 유량 단위 {unit_note}")

with st.sidebar:
    st.header("모의 조건")
    if not info.timeseries:
        st.error("모델에 강우 시계열([TIMESERIES])이 없습니다.")
        st.stop()
    default_idx = info.timeseries.index(info.default_timeseries) if info.default_timeseries in info.timeseries else 0
    timeseries = st.selectbox("강우 시나리오", info.timeseries, index=default_idx,
                              help="모델 [TIMESERIES]에 정의된 시계열을 모든 우량계에 적용합니다.")
    lo, hi, default, step = config.RAIN_FACTOR
    rain_factor = st.slider("강우 할증 배율", lo, hi, default, step, format="%.2f")
    lo, hi, default, step = config.IMPERV_DELTA
    imperv_delta = st.slider("불투수율 변화 (%p)", lo, hi, default, step, format="%+.0f",
                             help="모든 소유역에 더하고 0~100% 범위로 제한합니다.")
    lo, hi, default, step = config.ROUGHNESS_FACTOR
    roughness_factor = st.slider("관 조도계수 배율", lo, hi, default, step, format="%.2f",
                                 help="모든 관로의 Manning n에 곱합니다.")
    run = st.button("모델 실행", type="primary", width="stretch")

scenario = Scenario(timeseries, rain_factor, imperv_delta, roughness_factor)

if run:
    with st.spinner("SWMM 실행 중..."):
        try:
            st.session_state["result"] = (scenario, run_cached(scenario, config.MODEL_PATH))
        except Exception as e:  # engine errors are reported, not raised in the UI
            st.session_state.pop("result", None)
            st.error(f"모델 실행 중 오류가 발생했습니다: {e}")

if "result" not in st.session_state:
    st.info("왼쪽에서 조건을 정하고 **모델 실행**을 누르세요.")
    st.stop()

# Everything below only reads the stored result, so changing views never re-runs the model.
ran, result = st.session_state["result"]
if asdict(ran) != asdict(scenario):  # by value: a code reload redefines the Scenario class
    st.warning("조건이 바뀌었습니다. 아래 결과는 이전 조건으로 실행한 것입니다.")
st.subheader("실행 결과")
st.caption(f"강우 {ran.timeseries} × {ran.rain_factor:.2f} · 불투수율 {ran.imperv_delta:+.0f}%p · "
           f"조도 × {ran.roughness_factor:.2f}")

h = headline(result)
row1, row2 = st.columns(4), st.columns(4)
row1[0].metric("총 강우량 (mm)", f"{h['total_precipitation'][0]:,.1f}", border=True)
row1[1].metric("총 유출량 (mm)", f"{h['total_runoff'][0]:,.1f}", border=True)
row1[2].metric("방류구 첨두유량 (㎥/s)", f"{h['outfall_peak_flow'][0]:,.2f}", border=True,
               help=f"방류구 {h['outfall_peak_node']}")
row1[3].metric("첨두 발생시각", fmt_elapsed(h["outfall_peak_time"]), border=True,
               help="모의 시작 후 경과 시간 (방류구 첨두유량 기준)")
row2[0].metric("최대 관로 충만도 (%)", f"{h['max_fullness'][0]:,.0f}", border=True,
               help=f"관로 {h['max_fullness'][1]} · 수심/만관 수심, 보고 간격 기준")
row2[1].metric("월류 노드 수 (개)", f"{h['flooded_nodes']}", border=True)
row2[2].metric("총 월류량 (㎥)", f"{h['total_flood_volume'][0]:,.0f}", border=True)

# Analysis period shared by all tabs.
s = result.series
times = list(s["node_depth"].index)
span_days = (times[-1] - times[0]).days
tfmt = (lambda t: t.strftime("%m-%d %H:%M")) if span_days else (lambda t: t.strftime("%H:%M"))
t0, t1 = st.select_slider("분석 기간", options=times, value=(times[0], times[-1]), format_func=tfmt)
period = {k: v.loc[t0:t1] for k, v in s.items()}
step_s = result.report_step
if (t0, t1) == (times[0], times[-1]):
    # Whole run: use SWMM's routing-step totals, which the KPI cards also show.
    fl = result.summary[result.summary.category == "node_flooding"]
    flood_volume = pd.Series([to_si(v, u)[0] for v, u in zip(fl.value, fl.unit)], index=fl.element)
    flood_volume = flood_volume.reindex(period["node_flooding"].columns, fill_value=0.0)
else:
    # Part of the run: integrate the report-step flooding rates.
    flood_volume = period["node_flooding"].sum() * step_s

tab_hydro, tab_map, tab_point, tab_table = st.tabs(["수문곡선", "관망도", "지점별 결과", "결과표"])

with tab_hydro:
    st.plotly_chart(charts.hydrograph(period["rainfall"], period["outfall_flow"]), width="stretch")

with tab_map:
    if not info.link_paths:
        st.info("모델에 [COORDINATES]가 없어 관망도를 그릴 수 없습니다.")
    else:
        mode = st.radio("표시 기준", ["기간 최댓값", "특정 시각"], horizontal=True)
        if mode == "기간 최댓값":
            fullness = period["link_fullness"].max()
            flow = period["link_flow"].abs().max()
            depth = period["node_depth"].max()
            flood = flood_volume
            size_label, size_value = "월류량 (㎥)", flood
            node_hover = {n: f"최대수심 {depth[n]:.2f} m<br>월류량 {flood[n]:,.1f} ㎥" for n in depth.index}
            link_hover = {n: f"최대충만도 {fullness[n]:.0f} %<br>최대유량 {flow[n]:.3f} ㎥/s" for n in fullness.index}
        else:
            outflow = period["outfall_flow"].sum(axis=1)
            peak_t = outflow.idxmax() if len(outflow) else period["node_depth"].index[0]
            t = st.select_slider("시각", options=list(period["node_depth"].index), value=peak_t, format_func=tfmt,
                                 help="처음에는 기간 안의 방류구 첨두 시각을 보여 줍니다.")
            fullness = period["link_fullness"].loc[t]
            flow = period["link_flow"].loc[t]
            depth = period["node_depth"].loc[t]
            rate = period["node_flooding"].loc[t]
            size_label, size_value = "월류율 (㎥/s)", rate
            node_hover = {n: f"{tfmt(t)} 수심 {depth[n]:.2f} m<br>월류율 {rate[n]:.3f} ㎥/s" for n in depth.index}
            link_hover = {n: f"{tfmt(t)} 충만도 {fullness[n]:.0f} %<br>유량 {flow[n]:.3f} ㎥/s"
                          for n in fullness.index}
        st.caption(f"관로 색: 충만도(수심/만관 수심), 파랑 → 빨강 · 원 크기: {size_label}에 비례 "
                   "· 빨간 원: 월류 발생 · 마우스를 올리면 값이 보입니다.")
        st.plotly_chart(charts.network_map(info, fullness.to_dict(), size_value.to_dict(), node_hover, link_hover,
                                           size_label), width="stretch")

with tab_point:
    kind = st.radio("대상", ["노드", "관로"], horizontal=True)
    if kind == "노드":
        names = list(period["node_depth"].columns)
        name = st.selectbox("노드", names)
        options = {"수심": ("m", period["node_depth"]), "월류율": ("㎥/s", period["node_flooding"])}
        if name in period["outfall_flow"]:
            options["방류 유량"] = ("㎥/s", period["outfall_flow"])
    else:
        names = list(period["link_flow"].columns)
        name = st.selectbox("관로", names)
        options = {"유량": ("㎥/s", period["link_flow"]), "충만도": ("%", period["link_fullness"])}
    chosen = st.multiselect("변수", list(options), default=list(options))
    if chosen:
        st.plotly_chart(charts.element_series([(v, options[v][0], options[v][1][name]) for v in chosen]),
                        width="stretch")

with tab_table:
    st.caption(f"분석 기간 {tfmt(t0)} ~ {tfmt(t1)} 의 보고 간격({step_s // 60}분) 시계열 기준 값입니다. "
               "월류량은 전체 기간이면 SWMM 요약값, 일부 기간이면 월류율을 적분한 값입니다.")
    depth, flood = period["node_depth"], period["node_flooding"]
    nodes = pd.DataFrame({
        "노드": depth.columns,
        "유형": [info.node_types.get(n, "") for n in depth.columns],
        "최대수심 (m)": depth.max().values,
        "최대수심 시각": [tfmt(t) for t in depth.idxmax()],
        "월류량 (㎥)": flood_volume.values,
        "최대 월류율 (㎥/s)": flood.max().values,
    })
    flow, full = period["link_flow"].abs(), period["link_fullness"]
    links = pd.DataFrame({
        "관로": flow.columns,
        "최대유량 (㎥/s)": flow.max().values,
        "최대유량 시각": [tfmt(t) for t in flow.idxmax()],
        "최대충만도 (%)": full.max().values,
    })
    left, right = st.columns(2)
    with left:
        st.markdown("**노드별 최댓값**")
        st.dataframe(nodes.round(3), hide_index=True, width="stretch")
        st.download_button("노드 결과 CSV", nodes.to_csv(index=False).encode("utf-8-sig"),
                           "node_results.csv", "text/csv")
    with right:
        st.markdown("**관로별 최댓값**")
        st.dataframe(links.round(3), hide_index=True, width="stretch")
        st.download_button("관로 결과 CSV", links.to_csv(index=False).encode("utf-8-sig"),
                           "link_results.csv", "text/csv")
