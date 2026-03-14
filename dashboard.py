import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import threading
import time
import json
import socket
import uuid
from collections import deque
from datetime import datetime

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Arklow Bank Wind Park",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');

:root {
    --teal:    #00c4b4;
    --blue:    #0066ff;
    --orange:  #ff6b35;
    --red:     #ff3b3b;
    --green:   #00e676;
    --yellow:  #ffd600;
    --bg:      #0a0e1a;
    --surface: #111827;
    --border:  #1e2d40;
    --text:    #e2e8f0;
    --muted:   #64748b;
}

html, body, [data-testid="stAppViewContainer"] {
    background-color: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'DM Sans', sans-serif;
}

[data-testid="stSidebar"] {
    background-color: #0d1220 !important;
    border-right: 1px solid var(--border);
}

[data-testid="stHeader"] { background: transparent !important; }

h1, h2, h3 { font-family: 'Space Mono', monospace !important; }

/* Metric cards */
[data-testid="metric-container"] {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
}

[data-testid="stMetricValue"] {
    font-family: 'Space Mono', monospace !important;
    color: var(--teal) !important;
    font-size: 1.6rem !important;
}

[data-testid="stMetricLabel"] {
    color: var(--muted) !important;
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}

[data-testid="stMetricDelta"] { font-size: 0.75rem !important; }

/* Buttons */
.stButton > button {
    background: var(--surface) !important;
    border: 1px solid var(--teal) !important;
    color: var(--teal) !important;
    font-family: 'Space Mono', monospace !important;
    font-size: 0.75rem !important;
    border-radius: 4px !important;
    transition: all 0.2s;
}
.stButton > button:hover {
    background: var(--teal) !important;
    color: var(--bg) !important;
}

/* Sliders */
[data-testid="stSlider"] label {
    color: var(--text) !important;
    font-size: 0.8rem !important;
}

/* Selectbox */
[data-testid="stSelectbox"] label { color: var(--muted) !important; font-size: 0.75rem !important; }
.stSelectbox > div > div {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
}

/* Status badge */
.badge-ok    { color: var(--green);  font-family: monospace; font-size:0.8rem; }
.badge-warn  { color: var(--yellow); font-family: monospace; font-size:0.8rem; }
.badge-crit  { color: var(--red);    font-family: monospace; font-size:0.8rem; }
.badge-info  { color: var(--teal);   font-family: monospace; font-size:0.8rem; }

/* Alert box */
.alert-box {
    background: #1a0a0a;
    border-left: 3px solid var(--red);
    padding: 8px 14px;
    border-radius: 0 4px 4px 0;
    font-family: 'Space Mono', monospace;
    font-size: 0.72rem;
    color: #ff8080;
    margin: 4px 0;
}

.info-box {
    background: #0a1a1a;
    border-left: 3px solid var(--teal);
    padding: 8px 14px;
    border-radius: 0 4px 4px 0;
    font-family: 'Space Mono', monospace;
    font-size: 0.72rem;
    color: var(--teal);
    margin: 4px 0;
}

/* Section header */
.section-hdr {
    font-family: 'Space Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
    margin-bottom: 12px;
}

/* Live indicator */
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
.live-dot {
    display:inline-block; width:8px; height:8px;
    background:var(--green); border-radius:50%;
    animation: pulse 1.5s infinite;
    margin-right:6px;
}
.dead-dot {
    display:inline-block; width:8px; height:8px;
    background:var(--red); border-radius:50%;
    margin-right:6px;
}

/* Divider */
hr { border-color: var(--border) !important; }

/* Tab styling */
[data-baseweb="tab-list"] { background: var(--surface) !important; border-radius: 6px; }
[data-baseweb="tab"] { color: var(--muted) !important; font-family: 'Space Mono', monospace !important; font-size: 0.72rem !important; }
[aria-selected="true"] { color: var(--teal) !important; }
</style>
""", unsafe_allow_html=True)


# ── Backend startup (cached, runs once) ───────────────────────────────────────

@st.cache_resource
def start_backend():
    """Start turbine node, gateway and control centre once."""
    try:
        from turbine.turbine_node        import ArklowTurbineNode
        from satellite.leo_gateway       import KuBandGateway
        from ground_station.control_centre import ArklowControlCentre
        from channel.channel_model       import get_link

        link    = get_link(time_scale=60.0)
        turbine = ArklowTurbineNode()
        turbine.start()
        gw      = KuBandGateway(time_scale=60.0)
        gw.start()
        time.sleep(1.5)
        ctrl    = ArklowControlCentre()
        ctrl.start_link_monitor()

        return {"turbine": turbine, "gateway": gw,
                "ctrl": ctrl, "link": link, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Rolling history buffers ───────────────────────────────────────────────────

@st.cache_resource
def get_history():
    return {
        "time":         deque(maxlen=120),
        "wind":         deque(maxlen=120),
        "power":        deque(maxlen=120),
        "rpm":          deque(maxlen=120),
        "pitch":        deque(maxlen=120),
        "yaw":          deque(maxlen=120),
        "temp_gearbox": deque(maxlen=120),
        "temp_gen":     deque(maxlen=120),
        "temp_bearing": deque(maxlen=120),
        "vib_gbx":      deque(maxlen=120),
        "sec_events":   deque(maxlen=30),
        "comms_log":    deque(maxlen=40),
    }


# ── Helper: poll turbine directly ─────────────────────────────────────────────

def fetch_snapshot(ctrl):
    try:
        return ctrl.poll_sensors(via_gateway=False)
    except Exception:
        return None


def fetch_link_status(link):
    try:
        return link.status()
    except Exception:
        return {}


# ── Plotly chart theme ────────────────────────────────────────────────────────

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor ="rgba(0,0,0,0)",
    font=dict(family="Space Mono, monospace", color="#64748b", size=10),
    xaxis=dict(showgrid=True,  gridcolor="#1e2d40", gridwidth=1,
               zeroline=False, color="#64748b"),
    yaxis=dict(showgrid=True,  gridcolor="#1e2d40", gridwidth=1,
               zeroline=False, color="#64748b"),
    margin=dict(l=40, r=10, t=20, b=30),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=9)),
    hovermode="x unified",
)


def line_chart(x, y_series: dict, height=160, colors=None):
    """Build a compact plotly line chart."""
    fig = go.Figure()
    palette = ["#00c4b4", "#0066ff", "#ff6b35", "#ffd600", "#00e676"]
    for i, (name, y) in enumerate(y_series.items()):
        c = (colors or palette)[i % len(palette)]
        fig.add_trace(go.Scatter(
            x=list(x), y=list(y), name=name,
            mode="lines",
            line=dict(color=c, width=1.5),
            fill="tozeroy",
            fillcolor=c.replace(")", ",0.06)").replace("rgb", "rgba")
                      if c.startswith("rgb") else f"rgba({int(c[1:3],16)},{int(c[3:5],16)},{int(c[5:7],16)},0.06)",
        ))
    fig.update_layout(**CHART_LAYOUT, height=height)
    return fig


def gauge_chart(value, max_val, label, color="#00c4b4", height=140):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        number=dict(font=dict(family="Space Mono", color=color, size=22)),
        gauge=dict(
            axis=dict(range=[0, max_val], tickcolor="#1e2d40",
                      tickfont=dict(size=8, color="#64748b")),
            bar=dict(color=color, thickness=0.25),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            steps=[dict(range=[0, max_val*0.6], color="#0d1220"),
                   dict(range=[max_val*0.6, max_val*0.85], color="#131e2e"),
                   dict(range=[max_val*0.85, max_val], color="#1a1020")],
        ),
        title=dict(text=label, font=dict(size=9, color="#64748b",
                                          family="Space Mono")),
    ))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=10, r=10, t=10, b=5), height=height)
    return fig


# ── Wind rose ─────────────────────────────────────────────────────────────────

def wind_rose(direction_deg, speed_ms):
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    fig = go.Figure(go.Barpolar(
        r=[0.2]*8,
        theta=dirs,
        marker_color=["#1e2d40"]*8,
        opacity=0.5,
    ))
    # Arrow for current direction
    fig.add_trace(go.Scatterpolar(
        r=[0, speed_ms / 30],
        theta=[direction_deg, direction_deg],
        mode="lines+markers",
        line=dict(color="#00c4b4", width=3),
        marker=dict(size=[0, 10], color="#00c4b4",
                    symbol=["circle","arrow-up"]),
        showlegend=False,
    ))
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(visible=False, range=[0,1]),
            angularaxis=dict(color="#64748b", gridcolor="#1e2d40",
                             tickfont=dict(size=8)),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=5, r=5, t=5, b=5),
        height=140,
        showlegend=False,
    )
    return fig


# ── Satellite orbit arc ───────────────────────────────────────────────────────

def orbit_display(elevation_deg, visible):
    import math
    theta = [i for i in range(0, 361, 2)]
    # orbit circle
    rx, ry = 0.9, 0.6
    ox = [rx * math.cos(math.radians(t)) for t in theta]
    oy = [ry * math.sin(math.radians(t)) for t in theta]

    fig = go.Figure()
    # Earth
    fig.add_shape(type="circle", x0=-0.18, y0=-0.18, x1=0.18, y1=0.18,
                  fillcolor="#0d2040", line_color="#1e4080", line_width=2)
    # Orbit
    fig.add_trace(go.Scatter(x=ox, y=oy, mode="lines",
                              line=dict(color="#1e2d40", width=1, dash="dot"),
                              showlegend=False, hoverinfo="skip"))
    # Satellite position from elevation
    el_r = math.radians(max(elevation_deg, 0))
    sx = rx * math.cos(el_r - math.pi/2 + math.pi/4)
    sy = ry * math.sin(el_r - math.pi/2 + math.pi/4)
    sat_color = "#00e676" if visible else "#ff3b3b"
    fig.add_trace(go.Scatter(
        x=[sx], y=[sy], mode="markers+text",
        marker=dict(size=14, color=sat_color, symbol="diamond"),
        text=["SAT"], textposition="top center",
        textfont=dict(color=sat_color, size=8, family="Space Mono"),
        showlegend=False,
    ))
    # Ground station
    fig.add_trace(go.Scatter(
        x=[0], y=[-0.18], mode="markers+text",
        marker=dict(size=8, color="#0066ff", symbol="triangle-up"),
        text=["ARKLOW"], textposition="bottom center",
        textfont=dict(color="#0066ff", size=7, family="Space Mono"),
        showlegend=False,
    ))
    # LOS line if visible
    if visible:
        fig.add_trace(go.Scatter(
            x=[0, sx], y=[-0.18, sy], mode="lines",
            line=dict(color="#00c4b4", width=1, dash="dash"),
            showlegend=False, hoverinfo="skip",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, range=[-1.1,1.1]),
        yaxis=dict(visible=False, range=[-0.8,0.8], scaleanchor="x"),
        margin=dict(l=0, r=0, t=0, b=0),
        height=150,
    )
    return fig


# ════════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD
# ════════════════════════════════════════════════════════════════════════════════

def main():
    backend = start_backend()
    hist    = get_history()

    if not backend["ok"]:
        st.error(f"Backend failed to start: {backend.get('error')}")
        st.stop()

    ctrl = backend["ctrl"]
    link = backend["link"]

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("""
        <div style='text-align:center;padding:16px 0 8px'>
          <div style='font-family:Space Mono;font-size:0.65rem;
                      letter-spacing:0.2em;color:#64748b;'>ARKLOW BANK</div>
          <div style='font-family:Space Mono;font-size:1.1rem;
                      color:#00c4b4;font-weight:700;'>WIND PARK</div>
          <div style='font-family:Space Mono;font-size:0.6rem;
                      color:#64748b;margin-top:4px;'>GE 3.6-100 · T1 · ABWCP/1.0</div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        st.markdown('<div class="section-hdr">TURBINE CONTROL</div>',
                    unsafe_allow_html=True)

        pitch_val = st.slider("Blade Pitch (°)", 0.0, 90.0, 4.0, 0.5,
                               key="pitch_slider")
        if st.button("⟳  SEND PITCH CMD", use_container_width=True):
            ok = ctrl.command_pitch(pitch_val, via_gateway=False)
            msg = f"✓ Pitch → {pitch_val}°" if ok else "✗ Pitch cmd failed"
            hist["comms_log"].appendleft(
                f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        st.markdown("---")
        yaw_val = st.slider("Nacelle Yaw (°)", 0.0, 359.0, 225.0, 1.0,
                             key="yaw_slider")
        if st.button("⟳  SEND YAW CMD", use_container_width=True):
            ctrl.command_yaw(yaw_val)
            hist["comms_log"].appendleft(
                f"[{datetime.now().strftime('%H:%M:%S')}] → CMD_YAW {yaw_val:.0f}° (UDP)")

        st.markdown("---")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("🔴 E-STOP", use_container_width=True):
                try:
                    with ctrl._turbine_socket(9002) as s:
                        from utils.protocol import build_msg, tcp_send, MsgKind
                        cmd = build_msg(MsgKind.CMD_ESTOP,
                                        "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {})
                        tcp_send(s, cmd)
                    hist["comms_log"].appendleft(
                        f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 ESTOP SENT")
                except Exception as e:
                    st.error(str(e))

        with col_b:
            if st.button("↺ REFRESH", use_container_width=True):
                st.rerun()

        st.divider()
        st.markdown('<div class="section-hdr">SATELLITE PASS</div>',
                    unsafe_allow_html=True)
        if st.button("⊕  INJECT SAT PASS", use_container_width=True):
            link.inject_pass(duration_s=600, peak_el=55.0)
            hist["comms_log"].appendleft(
                f"[{datetime.now().strftime('%H:%M:%S')}] ★ Satellite pass injected")

        st.divider()
        st.markdown('<div class="section-hdr">OUTAGE TEST</div>',
                    unsafe_allow_html=True)
        outage_dur = st.slider("Duration (s)", 3, 30, 5, key="outage_dur")
        if st.button("⚡ INJECT OUTAGE", use_container_width=True):
            link.inject_outage(outage_dur)
            hist["comms_log"].appendleft(
                f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ Outage {outage_dur}s injected")

    # ── Fetch live data ───────────────────────────────────────────────────────
    snap = fetch_snapshot(ctrl)
    ls   = fetch_link_status(link)

    if snap:
        now = datetime.now().strftime("%H:%M:%S")
        hist["time"].append(now)
        hist["wind"].append(snap.get("wind_speed_ms", 0))
        hist["power"].append(snap.get("power_kw", 0))
        hist["rpm"].append(snap.get("rotor_rpm", 0))
        hist["pitch"].append(snap.get("pitch_actual_deg", 0))
        hist["yaw"].append(snap.get("yaw_actual_deg", 0))
        hist["temp_gearbox"].append(snap.get("temp_gearbox_c", 0))
        hist["temp_gen"].append(snap.get("temp_generator_c", 0))
        hist["temp_bearing"].append(snap.get("temp_main_bearing_c", 0))
        hist["vib_gbx"].append(snap.get("gearbox_vibration_g", 0))

    # ── Header ────────────────────────────────────────────────────────────────
    live_status = snap is not None
    dot = '<span class="live-dot"></span>' if live_status else '<span class="dead-dot"></span>'
    st.markdown(
        f"<h2 style='margin:0;font-size:1.1rem;color:#e2e8f0;'>"
        f"{dot}Arklow Bank Wind Park — Live Control Dashboard</h2>"
        f"<div style='font-family:Space Mono;font-size:0.65rem;color:#64748b;"
        f"margin-bottom:16px;'>52.47°N 5.56°W · GE 3.6-100 · T1 · ABWCP/1.0 · "
        f"{'ONLINE' if live_status else 'OFFLINE'}</div>",
        unsafe_allow_html=True
    )

    # ── Top KPI row ───────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5, k6, k7 = st.columns(7)

    wind_now  = snap.get("wind_speed_ms",  0) if snap else 0
    power_now = snap.get("power_kw",       0) if snap else 0
    rpm_now   = snap.get("rotor_rpm",      0) if snap else 0
    pitch_now = snap.get("pitch_actual_deg",0) if snap else 0
    yaw_now   = snap.get("yaw_actual_deg", 0) if snap else 0
    gbx_now   = snap.get("temp_gearbox_c", 0) if snap else 0
    fault     = snap.get("fault_code",     0) if snap else 0
    fault_desc= snap.get("fault_description","") if snap else ""

    with k1: st.metric("Wind Speed", f"{wind_now:.1f} m/s",
                        delta=f"{wind_now-9.8:+.1f} vs mean")
    with k2: st.metric("Power Output", f"{power_now:.0f} kW",
                        delta=f"{power_now/3600*100:.0f}% rated")
    with k3: st.metric("Rotor RPM", f"{rpm_now:.1f}")
    with k4: st.metric("Blade Pitch", f"{pitch_now:.1f}°")
    with k5: st.metric("Nacelle Yaw", f"{yaw_now:.0f}°")
    with k6: st.metric("Gearbox Temp", f"{gbx_now:.1f}°C",
                        delta=f"{'⚠' if gbx_now>70 else 'OK'}")
    with k7: st.metric("Fault Code", f"{fault}",
                        delta=fault_desc if fault_desc else "NOMINAL")

    st.divider()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "⚡  POWER & WIND",
        "🌡  TEMPERATURES",
        "🛰  SATELLITE LINK",
        "🔒  SECURITY",
        "📡  COMMS LOG",
    ])

    # ────────────────────────────────────────────────────────────────────────
    # TAB 1 — Power & Wind
    # ────────────────────────────────────────────────────────────────────────
    with tab1:
        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown('<div class="section-hdr">POWER OUTPUT  (kW)</div>',
                        unsafe_allow_html=True)
            if len(hist["power"]) > 1:
                fig = line_chart(hist["time"], {"Power (kW)": hist["power"]},
                                 height=180, colors=["#00c4b4"])
                fig.add_hline(y=3600, line_dash="dot",
                              line_color="#ff6b35", line_width=1,
                              annotation_text="Rated 3.6 MW",
                              annotation_font_color="#ff6b35",
                              annotation_font_size=9)
                st.plotly_chart(fig, use_container_width=True)

            st.markdown('<div class="section-hdr">WIND SPEED  (m/s)</div>',
                        unsafe_allow_html=True)
            if len(hist["wind"]) > 1:
                fig2 = line_chart(hist["time"],
                                  {"Wind (m/s)": hist["wind"]},
                                  height=160, colors=["#0066ff"])
                fig2.add_hline(y=9.8, line_dash="dot",
                               line_color="#64748b", line_width=1,
                               annotation_text="Mean 9.8 m/s",
                               annotation_font_color="#64748b",
                               annotation_font_size=9)
                fig2.add_hline(y=27.0, line_dash="dot",
                               line_color="#ff3b3b", line_width=1,
                               annotation_text="Cut-out 27 m/s",
                               annotation_font_color="#ff3b3b",
                               annotation_font_size=9)
                st.plotly_chart(fig2, use_container_width=True)

        with col2:
            st.markdown('<div class="section-hdr">WIND DIRECTION</div>',
                        unsafe_allow_html=True)
            wind_dir = snap.get("wind_dir_deg", 225) if snap else 225
            st.plotly_chart(wind_rose(wind_dir, wind_now),
                            use_container_width=True)
            st.markdown(
                f'<div style="text-align:center;font-family:Space Mono;'
                f'font-size:0.7rem;color:#64748b;">'
                f'{wind_dir:.0f}° · {wind_now:.1f} m/s</div>',
                unsafe_allow_html=True
            )

            st.markdown('<div class="section-hdr" style="margin-top:16px">ROTOR</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(gauge_chart(rpm_now, 22, "RPM", "#0066ff"),
                            use_container_width=True)

            st.markdown('<div class="section-hdr">PITCH ANGLE</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(gauge_chart(pitch_now, 90, "Pitch °", "#ff6b35"),
                            use_container_width=True)

        # Pitch & RPM history
        if len(hist["pitch"]) > 1:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown('<div class="section-hdr">PITCH HISTORY</div>',
                            unsafe_allow_html=True)
                st.plotly_chart(
                    line_chart(hist["time"], {"Pitch °": hist["pitch"]},
                               height=130, colors=["#ff6b35"]),
                    use_container_width=True
                )
            with c2:
                st.markdown('<div class="section-hdr">RPM HISTORY</div>',
                            unsafe_allow_html=True)
                st.plotly_chart(
                    line_chart(hist["time"], {"RPM": hist["rpm"]},
                               height=130, colors=["#0066ff"]),
                    use_container_width=True
                )

    # ────────────────────────────────────────────────────────────────────────
    # TAB 2 — Temperatures
    # ────────────────────────────────────────────────────────────────────────
    with tab2:
        if snap:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Gearbox",    f"{snap.get('temp_gearbox_c',0):.1f}°C")
                colour = "#ff3b3b" if snap.get("temp_gearbox_c",0) > 70 else "#00c4b4"
                st.plotly_chart(
                    gauge_chart(snap.get("temp_gearbox_c",0), 100,
                                "Gearbox °C", colour),
                    use_container_width=True
                )
            with c2:
                st.metric("Generator", f"{snap.get('temp_generator_c',0):.1f}°C")
                st.plotly_chart(
                    gauge_chart(snap.get("temp_generator_c",0), 120,
                                "Generator °C", "#ff6b35"),
                    use_container_width=True
                )
            with c3:
                st.metric("Main Bearing", f"{snap.get('temp_main_bearing_c',0):.1f}°C")
                st.plotly_chart(
                    gauge_chart(snap.get("temp_main_bearing_c",0), 80,
                                "Bearing °C", "#ffd600"),
                    use_container_width=True
                )
            with c4:
                st.metric("Nacelle", f"{snap.get('temp_nacelle_c',0):.1f}°C")
                st.plotly_chart(
                    gauge_chart(snap.get("temp_nacelle_c",0), 60,
                                "Nacelle °C", "#00e676"),
                    use_container_width=True
                )

        if len(hist["temp_gearbox"]) > 1:
            st.markdown('<div class="section-hdr">TEMPERATURE HISTORY</div>',
                        unsafe_allow_html=True)
            fig_t = line_chart(
                hist["time"],
                {"Gearbox":    hist["temp_gearbox"],
                 "Generator":  hist["temp_gen"],
                 "Bearing":    hist["temp_bearing"]},
                height=200,
                colors=["#00c4b4","#ff6b35","#ffd600"]
            )
            st.plotly_chart(fig_t, use_container_width=True)

        if snap:
            st.markdown('<div class="section-hdr">CONDITION MONITORING</div>',
                        unsafe_allow_html=True)
            cm1, cm2, cm3 = st.columns(3)
            with cm1:
                st.metric("Gearbox Vibration",
                          f"{snap.get('gearbox_vibration_g',0):.3f} g-rms",
                          delta="Normal" if snap.get("gearbox_vibration_g",0)<0.3 else "⚠ High")
            with cm2:
                st.metric("Blade Vibration",
                          f"{snap.get('blade_vibration_hz',0):.3f} Hz  (1P)")
            with cm3:
                st.metric("Tower Fore-Aft",
                          f"{snap.get('tower_fore_aft_mm',0):.1f} mm")

            if len(hist["vib_gbx"]) > 1:
                st.plotly_chart(
                    line_chart(hist["time"],
                               {"Gearbox Vib (g-rms)": hist["vib_gbx"]},
                               height=130, colors=["#ff3b3b"]),
                    use_container_width=True
                )

    # ────────────────────────────────────────────────────────────────────────
    # TAB 3 — Satellite Link
    # ────────────────────────────────────────────────────────────────────────
    with tab3:
        col_orb, col_stats = st.columns([1, 2])

        with col_orb:
            st.markdown('<div class="section-hdr">ORBITAL VIEW</div>',
                        unsafe_allow_html=True)
            el   = ls.get("elevation_deg", 0)
            vis  = ls.get("satellite_visible", False)
            st.plotly_chart(orbit_display(el, vis),
                            use_container_width=True)
            link_label = "🟢 LINK UP" if vis else "🔴 LINK DOWN"
            st.markdown(
                f'<div style="text-align:center;font-family:Space Mono;'
                f'font-size:0.8rem;color:{"#00e676" if vis else "#ff3b3b"};">'
                f'{link_label}</div>',
                unsafe_allow_html=True
            )

        with col_stats:
            st.markdown('<div class="section-hdr">KU-BAND LINK PARAMETERS</div>',
                        unsafe_allow_html=True)
            r1, r2, r3 = st.columns(3)
            with r1:
                st.metric("Elevation",    f"{ls.get('elevation_deg',0):.1f}°")
                st.metric("Slant Range",  f"{ls.get('slant_range_km',0):.0f} km")
            with r2:
                st.metric("Delay",        f"{ls.get('propagation_delay_ms',0):.2f} ms")
                st.metric("Path Loss",    f"{ls.get('path_loss_db',0):.1f} dB")
            with r3:
                st.metric("Doppler Shift",f"{ls.get('doppler_hz',0):.0f} Hz")
                st.metric("Rain Atten.",  f"{ls.get('rain_attenuation_db',0):.2f} dB")

            st.divider()

            r4, r5, r6 = st.columns(3)
            with r4:
                st.metric("Sea State", ls.get("sea_description","—"))
            with r5:
                st.metric("Wave Height", f"{ls.get('wave_height_m',0):.1f} m")
            with r6:
                per = ls.get("packet_error_rate", 0)
                st.metric("Packet Error Rate", f"{per*100:.2f}%")

            if ls.get("next_aos_s"):
                nxt = ls["next_aos_s"]
                st.markdown(
                    f'<div class="info-box">⏱ Next satellite pass in '
                    f'{nxt:.0f} simulated seconds '
                    f'({nxt/3600:.2f} simulated hours)</div>',
                    unsafe_allow_html=True
                )
            if ls.get("pass_remaining_s"):
                rem = ls["pass_remaining_s"]
                st.markdown(
                    f'<div class="info-box">🛰 Current pass — '
                    f'{rem:.0f}s remaining</div>',
                    unsafe_allow_html=True
                )

        st.divider()
        st.markdown('<div class="section-hdr">CHANNEL PARAMETERS</div>',
                    unsafe_allow_html=True)
        cp1, cp2, cp3, cp4 = st.columns(4)
        with cp1:
            st.markdown("""
            <div class='info-box'>
            Orbit altitude: 600 km<br>
            Orbital period: ~96.7 min<br>
            Sat velocity: 7,558 m/s
            </div>""", unsafe_allow_html=True)
        with cp2:
            st.markdown("""
            <div class='info-box'>
            Carrier: 14.5 GHz (Ku-band)<br>
            FSPL @ zenith: ~184 dB<br>
            Min elevation: 5°
            </div>""", unsafe_allow_html=True)
        with cp3:
            st.markdown(f"""
            <div class='info-box'>
            Location: 52.47°N 5.56°W<br>
            Irish Sea depth: 3–8 m<br>
            Sea State: {ls.get('sea_description','—')}
            </div>""", unsafe_allow_html=True)
        with cp4:
            st.markdown(f"""
            <div class='info-box'>
            Scintillation: {"ACTIVE" if ls.get("scintillation") else "None"}<br>
            Forced outage: {"YES" if ls.get("forced_outage") else "No"}<br>
            Link up: {"YES" if ls.get("link_up") else "NO"}
            </div>""", unsafe_allow_html=True)

    # ────────────────────────────────────────────────────────────────────────
    # TAB 4 — Security
    # ────────────────────────────────────────────────────────────────────────
    with tab4:
        st.markdown('<div class="section-hdr">INTRUSION DETECTION — LIVE TEST</div>',
                    unsafe_allow_html=True)

        from security.intrusion_detector import SecurityMonitor
        from utils.protocol import build_msg, MsgKind

        sec_col1, sec_col2 = st.columns([1, 1])

        with sec_col1:
            st.markdown("**Run Attack Simulations**")
            attack_type = st.selectbox("Select attack", [
                "Normal pitch command",
                "Out-of-range pitch (95°)",
                "Unauthorized ESTOP",
                "Replay attack",
                "Flood attack (60 msgs)",
            ], key="attack_sel")

            if st.button("▶  RUN ATTACK TEST", use_container_width=True):
                mon = SecurityMonitor()
                mon.trust("ARKLOW_CTRL_1")
                mon.trust("ARKLOW_GE3600_T1")

                def _mk(origin, kind, body):
                    return build_msg(kind, origin, "ARKLOW_GE3600_T1", body)

                result_lines = []

                if attack_type == "Normal pitch command":
                    m = _mk("ARKLOW_CTRL_1", MsgKind.CMD_PITCH, {"pitch_deg": 12.0})
                    ok, r = mon.inspect(m)
                    result_lines.append(("✓ ALLOW" if ok else "✗ BLOCK", r, ok))

                elif attack_type == "Out-of-range pitch (95°)":
                    m = _mk("ATTACKER_NODE", MsgKind.CMD_PITCH, {"pitch_deg": 95.0})
                    ok, r = mon.inspect(m)
                    result_lines.append(("✓ ALLOW" if ok else "✗ BLOCK", r, ok))

                elif attack_type == "Unauthorized ESTOP":
                    m = _mk("ROGUE_NODE", MsgKind.CMD_ESTOP, {})
                    ok, r = mon.inspect(m)
                    result_lines.append(("✓ ALLOW" if ok else "✗ BLOCK", r, ok))

                elif attack_type == "Replay attack":
                    m = _mk("ARKLOW_CTRL_1", MsgKind.CMD_PITCH, {"pitch_deg": 8.0})
                    mon.inspect(m)
                    ok, r = mon.inspect(m)
                    result_lines.append(("✓ ALLOW" if ok else "✗ BLOCK",
                                         f"REPLAY: {r}", ok))

                elif attack_type == "Flood attack (60 msgs)":
                    allowed = 0
                    for i in range(60):
                        fm = {**_mk("FLOOD_NODE", MsgKind.SENSOR_POLL, {}),
                              "mid": str(uuid.uuid4()), "seq": i}
                        if mon.inspect(fm)[0]:
                            allowed += 1
                    result_lines.append(
                        ("✗ BLOCKED", f"Flood: {allowed}/60 allowed, rest blocked",
                         False)
                    )

                st.session_state["sec_results"] = result_lines
                for label, reason, ok in result_lines:
                    colour = "#00e676" if ok else "#ff3b3b"
                    st.markdown(
                        f'<div style="font-family:Space Mono;font-size:0.8rem;'
                        f'color:{colour};padding:8px;background:#0d1220;'
                        f'border-radius:4px;margin:4px 0;">'
                        f'{label} — {reason}</div>',
                        unsafe_allow_html=True
                    )

        with sec_col2:
            st.markdown("**Security Policy (ABWCP/1.0)**")
            policies = [
                ("Rate Limiter",       "20 msg/s per source (token bucket)"),
                ("Replay Cache",       "5-minute msg_id deduplication window"),
                ("Command Bounds",     "Pitch 0–90° (GE 3.6-100 physical limits)"),
                ("Pitch Rate",         "Reject cmds < 100ms apart (servo limited)"),
                ("Unauthorized ESTOP", "Block from unregistered nodes"),
                ("Seq Injection",      "Flag jumps > 1000 in sequence number"),
            ]
            for name, detail in policies:
                st.markdown(
                    f'<div class="info-box"><b>{name}</b> — {detail}</div>',
                    unsafe_allow_html=True
                )

    # ────────────────────────────────────────────────────────────────────────
    # TAB 5 — Comms Log
    # ────────────────────────────────────────────────────────────────────────
    with tab5:
        st.markdown('<div class="section-hdr">COMMAND & TELEMETRY LOG</div>',
                    unsafe_allow_html=True)

        # Telemetry button
        if st.button("📥  FETCH 5s TELEMETRY BURST", use_container_width=False):
            def _do_telem():
                ctrl.subscribe_telemetry(duration_s=5.0)
                ts = ctrl.telemetry_summary()
                hist["comms_log"].appendleft(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"← TELEM {ts.get('frames',0)} frames | "
                    f"avg {ts.get('avg_power_kw',0):.0f} kW | "
                    f"peak {ts.get('peak_power_kw',0):.0f} kW"
                )
            threading.Thread(target=_do_telem, daemon=True).start()
            st.info("Telemetry subscription started in background…")

        # Discovery button
        if st.button("🔍  DISCOVER TURBINES", use_container_width=False):
            nodes = ctrl.discover_nodes(timeout_s=2.0)
            for n in nodes:
                hist["comms_log"].appendleft(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"← ANNOUNCE_REPLY: {n.get('node_id')} | "
                    f"{n.get('turbine_make')} | "
                    f"lat={n.get('location',{}).get('lat')}"
                )

        st.divider()

        if hist["comms_log"]:
            for entry in hist["comms_log"]:
                colour = "#ff8080" if "ESTOP" in entry or "OUTAGE" in entry \
                    else "#00c4b4" if "✓" in entry or "TELEM" in entry \
                    else "#64748b"
                st.markdown(
                    f'<div style="font-family:Space Mono;font-size:0.7rem;'
                    f'color:{colour};padding:3px 0;border-bottom:1px solid #111827;">'
                    f'{entry}</div>',
                    unsafe_allow_html=True
                )
        else:
            st.markdown('<div class="info-box">No commands sent yet — '
                        'use sidebar controls or tabs above.</div>',
                        unsafe_allow_html=True)

    # ── Auto-refresh footer ───────────────────────────────────────────────────
    st.divider()
    fc1, fc2, fc3 = st.columns([1, 2, 1])
    with fc1:
        st.markdown(
            f'<div style="font-family:Space Mono;font-size:0.6rem;color:#3a4a5a;">'
            f'ABWCP/1.0 · 14.5 GHz Ku-band · 600 km LEO</div>',
            unsafe_allow_html=True
        )
    with fc2:
        op_h = snap.get("op_hours", 0) if snap else 0
        st.markdown(
            f'<div style="font-family:Space Mono;font-size:0.6rem;'
            f'color:#3a4a5a;text-align:center;">'
            f'Turbine T1 · Op hours: {op_h:.0f} h · '
            f'Farm: 7 × GE 3.6-100 = 25.2 MW</div>',
            unsafe_allow_html=True
        )
    with fc3:
        st.markdown(
            f'<div style="font-family:Space Mono;font-size:0.6rem;'
            f'color:#3a4a5a;text-align:right;">'
            f'Last update: {datetime.now().strftime("%H:%M:%S")}</div>',
            unsafe_allow_html=True
        )

    # Auto-refresh every 2 seconds
    time.sleep(2)
    st.rerun()


if __name__ == "__main__":
    main()
