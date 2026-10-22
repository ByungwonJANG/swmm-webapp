"""Streamlit UI: set scenario conditions and run the SWMM model.

Run: streamlit run app/app.py
"""
from dataclasses import asdict

import streamlit as st

import config
from model_runner import Scenario, headline, model_info, run_scenario

st.set_page_config(page_title="SWMM 배수 시나리오 분석", layout="wide")
st.title("SWMM 기반 도시 배수 시나리오 분석")


@st.cache_data
def load_info(path):
    return model_info(path)


@st.cache_data(show_spinner=False)
def run_cached(scenario, path):
    return run_scenario(scenario, path)


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

ran, results = st.session_state["result"]
if asdict(ran) != asdict(scenario):  # by value: a code reload redefines the Scenario class
    st.warning("조건이 바뀌었습니다. 아래 결과는 이전 조건으로 실행한 것입니다.")
st.subheader("실행 결과")
st.caption(f"강우 {ran.timeseries} × {ran.rain_factor:.2f} · 불투수율 {ran.imperv_delta:+.0f}%p · "
           f"조도 × {ran.roughness_factor:.2f}")

h = headline(results)
cols = st.columns(3)
cols[0].metric("총 강우량 (mm)", f"{h['total_precipitation'][0]:,.1f}", border=True)
cols[1].metric("총 유출량 (mm)", f"{h['total_runoff'][0]:,.1f}", border=True)
cols[2].metric("방류구 첨두유량 (㎥/s)", f"{h['outfall_peak_flow'][0]:,.2f}", border=True)
cols = st.columns(3)
cols[0].metric("월류 노드 수 (개)", f"{h['flooded_nodes']}", border=True)
cols[1].metric("총 월류량 (㎥)", f"{h['total_flood_volume'][0]:,.0f}", border=True)

with st.expander("전체 결과 표 (모델 단위)"):
    st.dataframe(results, hide_index=True, width="stretch")
