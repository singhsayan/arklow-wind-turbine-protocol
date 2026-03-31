"""
dashboard.py
============
Arklow Bank Wind Park — SCADA Control & Monitoring System
==========================================================
PREMIUM LIGHT UI EDITION
3-DEVICE VERSION — connects directly to remote devices over network.

Setup:
  Laptop C (Turbine)  : python3 turbine/turbine_node.py
  Laptop B (Gateway)  : python3 satellite/leo_gateway.py
  Laptop A (This)     : streamlit run dashboard.py

Update TURBINE_HOST and GATEWAY_HOST below to match your hotspot IPs.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
import plotly.graph_objects as go
import threading, time, json, socket, uuid, math
from collections import deque
from datetime import datetime

# ══════════════════════════════════════════════════════════════════════════════
#  NETWORK CONFIG — UPDATE THESE TO YOUR HOTSPOT IPs
# ══════════════════════════════════════════════════════════════════════════════
TURBINE_HOST     = "10.34.233.17"
GATEWAY_HOST     = "10.34.233.83"
TURBINE_PORTS    = {"sensor":9001,"pitch":9002,"yaw":9003,
                    "telemetry":9004,"camera":9005,"discovery":9100}
GATEWAY_PORT     = 9010
LINK_STATUS_PORT = 9012
# ══════════════════════════════════════════════════════════════════════════════

from utils.protocol import build_msg, tcp_send, tcp_recv, MsgKind, ALL_NODES, PROTO_VERSION

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ABWCP — Arklow Bank SCADA",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── PREMIUM LIGHT SCADA CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:ital,wght@0,300;0,400;0,500&family=Syne:wght@400;500;600;700;800&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;1,9..40,300&display=swap');

:root {
    /* Core palette — light, airy, authoritative */
    --bg:           #F4F6F9;
    --bg2:          #FFFFFF;
    --bg3:          #EEF1F6;
    --panel:        #FFFFFF;
    --panel2:       #F8FAFC;
    --border:       #DDE3ED;
    --border2:      #C8D2E0;
    --border3:      #B0BDCE;

    /* Accent system */
    --blue:         #1A56DB;
    --blue2:        #1447B8;
    --blue-light:   #EEF3FF;
    --blue-mid:     #C7D7F8;
    --teal:         #0891B2;
    --teal-light:   #ECFEFF;
    --green:        #059669;
    --green-light:  #ECFDF5;
    --amber:        #B45309;
    --amber-light:  #FFFBEB;
    --amber-mid:    #FDE68A;
    --red:          #DC2626;
    --red-light:    #FEF2F2;
    --red-mid:      #FECACA;

    /* Typography */
    --text:         #0F1923;
    --text2:        #374151;
    --text3:        #6B7280;
    --text4:        #9CA3AF;
    --text5:        #D1D5DB;

    /* Fonts */
    --mono:         'DM Mono', monospace;
    --display:      'Syne', sans-serif;
    --body:         'DM Sans', sans-serif;

    /* Shadows */
    --shadow-sm:    0 1px 3px rgba(15,25,35,0.06), 0 1px 2px rgba(15,25,35,0.04);
    --shadow-md:    0 4px 12px rgba(15,25,35,0.08), 0 2px 4px rgba(15,25,35,0.04);
    --shadow-lg:    0 8px 24px rgba(15,25,35,0.1), 0 4px 8px rgba(15,25,35,0.06);
    --shadow-card:  0 0 0 1px var(--border), 0 2px 8px rgba(15,25,35,0.06);
}

* { box-sizing: border-box; }

html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"] {
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: var(--body) !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: var(--bg2) !important;
    border-right: 1px solid var(--border) !important;
    box-shadow: 2px 0 12px rgba(15,25,35,0.05) !important;
}
[data-testid="stSidebar"] > div { padding-top: 0 !important; }
[data-testid="stHeader"]  { display: none !important; }
[data-testid="stToolbar"] { display: none !important; }

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: var(--panel) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    padding: 14px 16px !important;
    box-shadow: var(--shadow-sm) !important;
    transition: box-shadow 0.2s, border-color 0.2s !important;
    position: relative;
    overflow: hidden;
}
[data-testid="metric-container"]::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, var(--blue), var(--teal));
    border-radius: 8px 8px 0 0;
}
[data-testid="metric-container"]:hover {
    box-shadow: var(--shadow-md) !important;
    border-color: var(--border2) !important;
}
[data-testid="stMetricValue"] {
    font-family: var(--mono) !important;
    color: var(--text) !important;
    font-size: 1.5rem !important;
    font-weight: 500 !important;
    letter-spacing: -0.01em !important;
}
[data-testid="stMetricLabel"] {
    font-family: var(--mono) !important;
    color: var(--text3) !important;
    font-size: 0.58rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.14em !important;
    font-weight: 400 !important;
}
[data-testid="stMetricDelta"] {
    font-family: var(--mono) !important;
    font-size: 0.62rem !important;
    font-weight: 400 !important;
}

/* ── Buttons ── */
.stButton > button {
    background: var(--panel) !important;
    border: 1px solid var(--border2) !important;
    color: var(--text2) !important;
    font-family: var(--mono) !important;
    font-size: 0.65rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.1em !important;
    border-radius: 6px !important;
    text-transform: uppercase !important;
    transition: all 0.15s ease !important;
    padding: 8px 14px !important;
    box-shadow: var(--shadow-sm) !important;
}
.stButton > button:hover {
    background: var(--blue-light) !important;
    border-color: var(--blue) !important;
    color: var(--blue) !important;
    box-shadow: 0 0 0 3px rgba(26,86,219,0.12) !important;
}
.stButton > button:active {
    background: var(--blue-mid) !important;
    transform: translateY(1px) !important;
}

/* Danger button */
.danger-btn .stButton > button {
    background: var(--red-light) !important;
    border-color: var(--red) !important;
    color: var(--red) !important;
    font-weight: 600 !important;
}
.danger-btn .stButton > button:hover {
    background: var(--red-mid) !important;
    box-shadow: 0 0 0 3px rgba(220,38,38,0.15) !important;
}

/* ── Sliders ── */
[data-testid="stSlider"] label {
    font-family: var(--mono) !important;
    color: var(--text3) !important;
    font-size: 0.62rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
    font-weight: 400 !important;
}
[data-testid="stSlider"] [data-testid="stMarkdownContainer"] p {
    font-family: var(--mono) !important;
    font-size: 0.6rem !important;
    color: var(--text4) !important;
}

/* ── Selectbox ── */
[data-testid="stSelectbox"] label {
    font-family: var(--mono) !important;
    color: var(--text3) !important;
    font-size: 0.62rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
}
.stSelectbox > div > div {
    background: var(--panel) !important;
    border: 1px solid var(--border2) !important;
    border-radius: 6px !important;
    color: var(--text) !important;
    font-family: var(--mono) !important;
    font-size: 0.75rem !important;
    box-shadow: var(--shadow-sm) !important;
}

/* ── Tabs ── */
[data-baseweb="tab-list"] {
    background: var(--bg2) !important;
    border-bottom: 1px solid var(--border) !important;
    gap: 0 !important;
    border-radius: 0 !important;
    box-shadow: 0 1px 0 var(--border) !important;
}
[data-baseweb="tab"] {
    font-family: var(--mono) !important;
    font-size: 0.62rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    color: var(--text3) !important;
    padding: 12px 22px !important;
    border-radius: 0 !important;
    border-right: 1px solid var(--border) !important;
    transition: all 0.12s ease !important;
}
[data-baseweb="tab"]:hover {
    color: var(--text) !important;
    background: var(--bg3) !important;
}
[aria-selected="true"] {
    color: var(--blue) !important;
    background: var(--bg2) !important;
    border-bottom: 2px solid var(--blue) !important;
    font-weight: 600 !important;
}

/* ── Divider ── */
hr {
    border: none !important;
    border-top: 1px solid var(--border) !important;
    margin: 14px 0 !important;
}

/* ── Status badges ── */
@keyframes pulse-green {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(5,150,105,0.4); }
    50%       { opacity: 0.9; box-shadow: 0 0 0 4px rgba(5,150,105,0); }
}
@keyframes blink-red {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
}
@keyframes blink-amber {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.4; }
}

.status-online {
    display: inline-flex; align-items: center; gap: 7px;
    font-family: var(--mono); font-size: 0.62rem; font-weight: 500;
    color: var(--green); letter-spacing: 0.08em;
}
.status-online::before {
    content: ''; width: 7px; height: 7px; border-radius: 50%;
    background: var(--green); flex-shrink: 0;
    animation: pulse-green 2.2s ease-in-out infinite;
}
.status-offline {
    display: inline-flex; align-items: center; gap: 7px;
    font-family: var(--mono); font-size: 0.62rem; font-weight: 500;
    color: var(--red); letter-spacing: 0.08em;
}
.status-offline::before {
    content: ''; width: 7px; height: 7px; border-radius: 50%;
    background: var(--red); flex-shrink: 0;
    animation: blink-red 1s step-end infinite;
}
.status-warn {
    display: inline-flex; align-items: center; gap: 7px;
    font-family: var(--mono); font-size: 0.62rem; font-weight: 500;
    color: var(--amber); letter-spacing: 0.08em;
}
.status-warn::before {
    content: ''; width: 7px; height: 7px; border-radius: 50%;
    background: var(--amber); flex-shrink: 0;
    animation: blink-amber 0.7s step-end infinite;
}

/* ── Info / alert boxes ── */
.scada-info {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-left: 3px solid var(--teal);
    border-radius: 0 6px 6px 0;
    padding: 9px 13px;
    font-family: var(--mono);
    font-size: 0.65rem;
    color: var(--text2);
    margin: 4px 0;
    line-height: 1.75;
}
.scada-alert {
    background: var(--red-light);
    border: 1px solid var(--red-mid);
    border-left: 3px solid var(--red);
    border-radius: 0 6px 6px 0;
    padding: 9px 13px;
    font-family: var(--mono);
    font-size: 0.65rem;
    color: var(--red);
    margin: 4px 0;
    line-height: 1.75;
}
.scada-ok {
    background: var(--green-light);
    border: 1px solid #A7F3D0;
    border-left: 3px solid var(--green);
    border-radius: 0 6px 6px 0;
    padding: 9px 13px;
    font-family: var(--mono);
    font-size: 0.65rem;
    color: #065F46;
    margin: 4px 0;
    line-height: 1.75;
}
.scada-warn {
    background: var(--amber-light);
    border: 1px solid var(--amber-mid);
    border-left: 3px solid var(--amber);
    border-radius: 0 6px 6px 0;
    padding: 9px 13px;
    font-family: var(--mono);
    font-size: 0.65rem;
    color: #92400E;
    margin: 4px 0;
    line-height: 1.75;
}

/* ── Section labels ── */
.sys-label {
    font-family: var(--mono);
    font-size: 0.55rem;
    font-weight: 500;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--text4);
    border-bottom: 1px solid var(--border);
    padding-bottom: 5px;
    margin-bottom: 12px;
}

/* ── Sidebar brand ── */
.sidebar-brand {
    background: linear-gradient(160deg, #0F1923 0%, #1A2B3C 100%);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 22px 18px 18px;
    margin-bottom: 0;
}

/* ── Log entries ── */
.log-entry {
    font-family: var(--mono);
    font-size: 0.62rem;
    padding: 5px 10px;
    border-bottom: 1px solid var(--border);
    color: var(--text2);
    line-height: 1.55;
    background: var(--bg2);
}
.log-entry.ok   { color: #065F46; background: #F0FDF4; }
.log-entry.err  { color: var(--red); background: var(--red-light); }
.log-entry.warn { color: #92400E; background: var(--amber-light); }

/* ── Panel card ── */
.panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 18px;
    box-shadow: var(--shadow-sm);
}

/* ── Stat chip ── */
.stat-chip {
    display: inline-flex; align-items: center; gap: 5px;
    background: var(--bg3);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 3px 8px;
    font-family: var(--mono);
    font-size: 0.58rem;
    color: var(--text3);
    font-weight: 400;
}

/* ── Streamlit overrides ── */
.stMarkdown p {
    font-family: var(--body) !important;
    color: var(--text2) !important;
}
[data-testid="stNotification"] {
    border-radius: 6px !important;
}

/* ── Spinner ── */
.stSpinner > div {
    border-top-color: var(--blue) !important;
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  NETWORK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def tcp_connect(host, port, timeout=4.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    return s

def is_reachable(host, port, timeout=1.5):
    try:
        with tcp_connect(host, port, timeout): return True
    except: return False

def poll_sensors():
    try:
        with tcp_connect(TURBINE_HOST, TURBINE_PORTS["sensor"]) as s:
            tcp_send(s, build_msg(MsgKind.SENSOR_POLL, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {}))
            r = tcp_recv(s)
            return r["body"] if r and r.get("kind") == MsgKind.SENSOR_REPLY else None
    except: return None

def send_pitch_direct(deg):
    try:
        with tcp_connect(TURBINE_HOST, TURBINE_PORTS["pitch"]) as s:
            tcp_send(s, build_msg(MsgKind.CMD_PITCH, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {"pitch_deg": deg}))
            r = tcp_recv(s)
            return r is not None and r.get("body", {}).get("accepted", False)
    except: return False

def send_pitch_gateway(deg):
    try:
        with tcp_connect(GATEWAY_HOST, GATEWAY_PORT) as s:
            tcp_send(s, build_msg(MsgKind.CMD_PITCH, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {"pitch_deg": deg}))
            r = tcp_recv(s)
            if r is None: return False, "NO_RESPONSE"
            if r.get("kind") == MsgKind.CONFIRM: return True, "RELAYED_VIA_SATELLITE"
            return False, r.get("body", {}).get("reason", "UNKNOWN")
    except Exception as e: return False, str(e)

def send_yaw_udp(deg):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2.0)
            cmd = build_msg(MsgKind.CMD_YAW, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {"yaw_deg": deg})
            s.sendto(json.dumps(cmd).encode(), (TURBINE_HOST, TURBINE_PORTS["yaw"]))
            try:
                data, _ = s.recvfrom(65535)
                return json.loads(data.decode()).get("body", {}).get("yaw_actual_deg")
            except socket.timeout: return None
    except: return None

def send_estop():
    try:
        with tcp_connect(TURBINE_HOST, TURBINE_PORTS["pitch"]) as s:
            tcp_send(s, build_msg(MsgKind.CMD_ESTOP, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {}))
            tcp_recv(s); return True
    except: return False

def discover_nodes():
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(2.0)
            ann = build_msg(MsgKind.ANNOUNCE, "ARKLOW_CTRL_1", ALL_NODES, {"protocol": PROTO_VERSION})
            s.sendto(json.dumps(ann).encode(), ("255.255.255.255", TURBINE_PORTS["discovery"]))
            t = time.time()
            while time.time() - t < 2.0:
                try:
                    data, addr = s.recvfrom(65535)
                    r = json.loads(data.decode())
                    if r.get("kind") == MsgKind.ANNOUNCE_REPLY:
                        r["body"]["_ip"] = addr[0]; found.append(r["body"])
                except socket.timeout: break
    except: pass
    return found

def negotiate():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3.0)
            offer = build_msg(MsgKind.OFFER, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1",
                              {"requested_services": ["sensor_polling","pitch_control","yaw_control","telemetry_stream"]})
            s.sendto(json.dumps(offer).encode(), (TURBINE_HOST, TURBINE_PORTS["discovery"]))
            data, _ = s.recvfrom(65535)
            r = json.loads(data.decode())
            return r["body"] if r.get("kind") == MsgKind.OFFER_REPLY else None
    except: return None

def commit_session():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3.0)
            sid = str(uuid.uuid4())[:8].upper()
            c = build_msg(MsgKind.COMMIT, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1",
                          {"session_id": sid, "agreed_services": ["sensor_polling","pitch_control","yaw_control"], "duration_s": 3600})
            s.sendto(json.dumps(c).encode(), (TURBINE_HOST, TURBINE_PORTS["discovery"]))
            data, _ = s.recvfrom(65535)
            r = json.loads(data.decode())
            return sid if r.get("kind") == MsgKind.COMMIT_ACK else None
    except: return None

def subscribe_telemetry_burst(seconds=4.0):
    frames = []
    try:
        with tcp_connect(TURBINE_HOST, TURBINE_PORTS["telemetry"], timeout=10.0) as s:
            tcp_send(s, build_msg(MsgKind.TELEM_START, "ARKLOW_CTRL_1", "ARKLOW_GE3600_T1", {}))
            ack = tcp_recv(s)
            if ack and ack.get("kind") == MsgKind.CONFIRM:
                s.settimeout(3.0)
                start = time.time()
                while time.time() - start < seconds:
                    m = tcp_recv(s)
                    if m and m.get("kind") == MsgKind.TELEM_FRAME:
                        frames.append(m["body"].get("data", {}))
    except: pass
    return frames


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED STATE
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def get_state():
    return {
        "link":     {},
        "log":      deque(maxlen=60),
        "listener": False,
        "hist": {k: deque(maxlen=180) for k in
                 ["time","wind","power","rpm","pitch","yaw",
                  "temp_gearbox","temp_gen","temp_bearing","vib_gbx"]},
    }

def start_link_listener(st8):
    if st8["listener"]: return
    st8["listener"] = True
    def _l():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("", LINK_STATUS_PORT)); s.settimeout(2.0)
                while True:
                    try:
                        data, _ = s.recvfrom(65535)
                        m = json.loads(data.decode())
                        if m.get("kind") == MsgKind.LINK_STATUS:
                            st8["link"] = m["body"]
                    except socket.timeout: continue
        except: pass
    threading.Thread(target=_l, daemon=True, name="LinkListener").start()


# ══════════════════════════════════════════════════════════════════════════════
#  CHART BUILDERS — light theme
# ══════════════════════════════════════════════════════════════════════════════

BASE_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="'DM Mono', monospace", color="#9CA3AF", size=9),
    xaxis=dict(showgrid=True, gridcolor="rgba(209,213,219,0.7)", gridwidth=1,
               zeroline=False, color="#9CA3AF", showticklabels=True,
               tickfont=dict(size=8), nticks=6,
               linecolor="rgba(209,213,219,0.5)"),
    yaxis=dict(showgrid=True, gridcolor="rgba(209,213,219,0.7)", gridwidth=1,
               zeroline=False, color="#9CA3AF",
               tickfont=dict(size=8),
               linecolor="rgba(209,213,219,0.5)"),
    margin=dict(l=44, r=8, t=8, b=28),
    legend=dict(bgcolor="rgba(255,255,255,0.8)", font=dict(size=8), x=0, y=1,
                bordercolor="rgba(209,213,219,0.5)", borderwidth=1),
    hovermode="x unified",
)

def fill_rgba(hex_color, alpha=0.08):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[:2],16)},{int(h[2:4],16)},{int(h[4:],16)},{alpha})"

def sparkline(x, ys, height=150, colors=None, reflines=None):
    palette = ["#1A56DB","#0891B2","#059669","#B45309","#7C3AED"]
    fig = go.Figure()
    for i, (name, y) in enumerate(ys.items()):
        c = (colors or palette)[i % len(palette)]
        fig.add_trace(go.Scatter(
            x=list(x), y=list(y), name=name, mode="lines",
            line=dict(color=c, width=1.8),
            fill="tozeroy", fillcolor=fill_rgba(c, 0.07),
        ))
    if reflines:
        for val, label, color in reflines:
            fig.add_hline(y=val, line_dash="dot", line_color=color,
                          line_width=1.2, annotation_text=label,
                          annotation_font_color=color, annotation_font_size=8,
                          annotation_position="right")
    fig.update_layout(**BASE_LAYOUT, height=height)
    return fig

def gauge(val, mx, label, color="#1A56DB", height=130):
    pct = min(val / mx, 1.0) if mx > 0 else 0
    bar_color = "#DC2626" if pct > 0.85 else "#B45309" if pct > 0.65 else color
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val,
        number=dict(font=dict(family="'DM Mono',monospace",
                               color=bar_color, size=20),
                    suffix=""),
        gauge=dict(
            axis=dict(range=[0, mx], tickcolor="#D1D5DB",
                      tickfont=dict(size=7, color="#9CA3AF"), nticks=5),
            bar=dict(color=bar_color, thickness=0.28),
            bgcolor="rgba(0,0,0,0)", borderwidth=0,
            steps=[
                dict(range=[0, mx*0.5],   color="rgba(244,246,249,0.9)"),
                dict(range=[mx*0.5, mx*0.75], color="rgba(238,241,246,0.7)"),
                dict(range=[mx*0.75, mx*0.9], color="rgba(253,230,138,0.25)"),
                dict(range=[mx*0.9, mx],       color="rgba(254,202,202,0.3)"),
            ],
            threshold=dict(line=dict(color=bar_color, width=2),
                           thickness=0.8, value=val),
        ),
        title=dict(text=label, font=dict(size=8, color="#9CA3AF",
                                          family="'DM Mono',monospace")),
    ))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=8, r=8, t=8, b=4), height=height)
    return fig

def wind_compass(deg, spd):
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    fig = go.Figure(go.Barpolar(
        r=[0.15]*8, theta=dirs,
        marker_color=["rgba(238,241,246,0.9)"]*8,
        marker_line_color=["#C8D2E0"]*8,
        marker_line_width=1.2,
        opacity=1,
    ))
    fig.add_trace(go.Scatterpolar(
        r=[0, min(spd/30, 0.9)],
        theta=[deg, deg],
        mode="lines+markers",
        line=dict(color="#1A56DB", width=2.5),
        marker=dict(size=[0, 9], color="#1A56DB", symbol=["circle","arrow-up"]),
        showlegend=False,
    ))
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(visible=False, range=[0, 1]),
            angularaxis=dict(
                color="#9CA3AF",
                gridcolor="rgba(200,210,224,0.6)",
                tickfont=dict(size=8, family="'DM Mono',monospace", color="#6B7280"),
                linecolor="#DDE3ED",
            ),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=8),
        height=145, showlegend=False,
    )
    return fig

def orbit_vis(el_deg, visible):
    theta = list(range(0, 361, 3))
    rx, ry = 0.85, 0.55
    ox = [rx * math.cos(math.radians(t)) for t in theta]
    oy = [ry * math.sin(math.radians(t)) for t in theta]

    fig = go.Figure()
    # Earth
    fig.add_shape(type="circle", x0=-0.22, y0=-0.22, x1=0.22, y1=0.22,
                  fillcolor="rgba(199,215,248,0.35)",
                  line_color="rgba(26,86,219,0.4)", line_width=1.5)
    fig.add_shape(type="circle", x0=-0.16, y0=-0.16, x1=0.16, y1=0.16,
                  fillcolor="rgba(199,215,248,0.5)",
                  line_color="rgba(26,86,219,0.2)", line_width=1)
    # Orbit path
    fig.add_trace(go.Scatter(x=ox, y=oy, mode="lines",
                              line=dict(color="rgba(200,210,224,0.8)", width=1.2, dash="dot"),
                              showlegend=False, hoverinfo="skip"))
    # Satellite
    el = math.radians(max(el_deg, 0))
    sx = rx * math.cos(el - math.pi/2 + math.pi/4)
    sy = ry * math.sin(el - math.pi/2 + math.pi/4)
    sc = "#059669" if visible else "#DC2626"
    fig.add_trace(go.Scatter(x=[sx], y=[sy], mode="markers",
                              marker=dict(size=22, color=sc, opacity=0.1, symbol="circle"),
                              showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=[sx], y=[sy], mode="markers+text",
                              marker=dict(size=10, color=sc, symbol="diamond",
                                          line=dict(color="rgba(255,255,255,0.6)", width=1.5)),
                              text=["SAT"], textposition="top center",
                              textfont=dict(color=sc, size=7,
                                            family="'DM Mono',monospace"),
                              showlegend=False))
    # Ground station
    fig.add_trace(go.Scatter(x=[0], y=[-0.18], mode="markers+text",
                              marker=dict(size=7, color="#0891B2", symbol="triangle-up"),
                              text=["ARKLOW"], textposition="bottom center",
                              textfont=dict(color="#0891B2", size=6,
                                            family="'DM Mono',monospace"),
                              showlegend=False))
    if visible:
        fig.add_trace(go.Scatter(
            x=[0, sx], y=[-0.18, sy], mode="lines",
            line=dict(color="rgba(5,150,105,0.3)", width=1.5, dash="dash"),
            showlegend=False, hoverinfo="skip"))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, range=[-1.15, 1.15]),
        yaxis=dict(visible=False, range=[-0.85, 0.85], scaleanchor="x"),
        margin=dict(l=0, r=0, t=0, b=0), height=160,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st8  = get_state()
    start_link_listener(st8)
    hist = st8["hist"]
    log  = st8["log"]
    ls   = st8["link"]

    def add_log(msg, kind="info"):
        log.appendleft({"t": datetime.now().strftime("%H:%M:%S"), "msg": msg, "k": kind})

    turbine_up = is_reachable(TURBINE_HOST, TURBINE_PORTS["sensor"])
    gateway_up = is_reachable(GATEWAY_HOST, GATEWAY_PORT)

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(f"""
        <div class="sidebar-brand">
            <div style="font-family:var(--mono);font-size:.5rem;letter-spacing:.3em;
                        color:rgba(255,255,255,0.35);text-transform:uppercase;margin-bottom:8px;">
                SCADA / REV 1.0
            </div>
            <div style="font-family:var(--display);font-size:1.35rem;font-weight:800;
                        color:#FFFFFF;letter-spacing:.02em;line-height:1.15;">
                ARKLOW BANK<br>
                <span style="color:#60A5FA;font-weight:600;">WIND PARK</span>
            </div>
            <div style="font-family:var(--mono);font-size:.55rem;
                        color:rgba(255,255,255,0.3);margin-top:10px;letter-spacing:.1em;">
                GE 3.6-100 &nbsp;·&nbsp; T1 &nbsp;·&nbsp; ABWCP/1.0
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="sys-label" style="margin-top:16px">Network Status</div>',
                    unsafe_allow_html=True)
        t_cls = "status-online" if turbine_up else "status-offline"
        g_cls = "status-online" if gateway_up else "status-offline"
        st.markdown(f"""
        <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;
                    background:var(--bg3);border:1px solid var(--border);
                    border-radius:8px;padding:10px 12px;">
            <div class="{t_cls}">TURBINE &nbsp; {TURBINE_HOST}</div>
            <div class="{g_cls}">GATEWAY &nbsp; {GATEWAY_HOST}</div>
            <div class="status-online">GROUND &nbsp; (LOCAL)</div>
        </div>
        """, unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="sys-label">Blade Pitch Control</div>',
                    unsafe_allow_html=True)
        pitch_val = st.slider("Target pitch (deg)", 0.0, 90.0, 4.0, 0.5, key="ps",
                               label_visibility="visible")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("DIRECT\nTCP", use_container_width=True, key="pd"):
                ok = send_pitch_direct(pitch_val)
                add_log(f"PITCH {pitch_val}deg direct -> {'ACK' if ok else 'FAIL'}",
                        "ok" if ok else "err")
        with c2:
            if st.button("VIA SAT\nGW", use_container_width=True, key="pg"):
                ok, why = send_pitch_gateway(pitch_val)
                add_log(f"PITCH {pitch_val}deg via GW -> {'ACK' if ok else why}",
                        "ok" if ok else "warn")
        st.divider()

        st.markdown('<div class="sys-label">Nacelle Yaw (UDP)</div>',
                    unsafe_allow_html=True)
        yaw_val = st.slider("Target bearing (deg)", 0.0, 359.0, 225.0, 1.0, key="ys",
                             label_visibility="visible")
        if st.button("SEND YAW COMMAND", use_container_width=True, key="yc"):
            actual = send_yaw_udp(yaw_val)
            add_log(f"YAW {yaw_val:.0f}deg UDP -> actual {actual}deg" if actual
                    else f"YAW {yaw_val:.0f}deg UDP sent", "ok")
        st.divider()

        st.markdown('<div class="sys-label">Emergency Controls</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="danger-btn">', unsafe_allow_html=True)
        if st.button("EMERGENCY STOP", use_container_width=True, key="es"):
            ok = send_estop()
            add_log("EMERGENCY STOP ACTIVATED" if ok else "ESTOP FAILED",
                    "err" if not ok else "warn")
        st.markdown('</div>', unsafe_allow_html=True)
        st.divider()

        if st.button("REFRESH", use_container_width=True, key="ref"):
            st.rerun()

        st.markdown(f"""
        <div style="margin-top:auto;padding:12px 0 4px;
                    font-family:var(--mono);font-size:.53rem;
                    color:var(--text4);line-height:2;letter-spacing:.06em;">
            PROTOCOL: ABWCP/1.0<br>
            CARRIER: 14.5 GHz KU-BAND<br>
            ORBIT ALT: 600 KM LEO<br>
            LOCATION: 52.47N 5.56W<br>
            FARM: 7 x GE 3.6-100
        </div>
        """, unsafe_allow_html=True)

    # ── FETCH LIVE DATA ───────────────────────────────────────────────────────
    snap = poll_sensors()
    now_str = datetime.now().strftime("%H:%M:%S")

    if snap:
        hist["time"].append(now_str)
        for k, sk in [("wind","wind_speed_ms"),("power","power_kw"),
                      ("rpm","rotor_rpm"),("pitch","pitch_actual_deg"),
                      ("yaw","yaw_actual_deg"),("temp_gearbox","temp_gearbox_c"),
                      ("temp_gen","temp_generator_c"),
                      ("temp_bearing","temp_main_bearing_c"),
                      ("vib_gbx","gearbox_vibration_g")]:
            hist[k].append(snap.get(sk, 0))

    # ── TOP HEADER BAR ────────────────────────────────────────────────────────
    online  = snap is not None
    link_up = ls.get("satellite_visible", False)
    fault   = snap.get("fault_code", 0) if snap else 0

    h_sys = "status-online" if online   else "status-offline"
    h_sat = "status-online" if link_up  else "status-warn"
    h_flt = "status-offline" if fault   else "status-online"

    fault_label = f"FAULT F{fault}" if fault else "NO FAULT"

    st.markdown(f"""
    <div style="display:flex;align-items:center;justify-content:space-between;
                padding:12px 20px;
                background:var(--bg2);
                border:1px solid var(--border);
                border-radius:10px;
                box-shadow:var(--shadow-sm);
                margin-bottom:18px;">
        <div style="display:flex;align-items:center;gap:16px;">
            <div style="width:3px;height:36px;background:linear-gradient(180deg,var(--blue),var(--teal));
                        border-radius:2px;flex-shrink:0;"></div>
            <div>
                <div style="font-family:var(--display);font-size:1.05rem;font-weight:700;
                            color:var(--text);letter-spacing:.03em;line-height:1;">
                    ARKLOW BANK WIND PARK
                </div>
                <div style="font-family:var(--mono);font-size:.56rem;color:var(--text4);
                            letter-spacing:.12em;margin-top:3px;">
                    GE 3.6-100 &nbsp;·&nbsp; TURBINE T1 &nbsp;·&nbsp; 25.2 MW INSTALLED
                </div>
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:24px;">
            <div class="{h_sys}">SYS {'ONLINE' if online else 'OFFLINE'}</div>
            <div class="{h_sat}">KU-BAND {'UP' if link_up else 'STANDBY'}</div>
            <div class="{h_flt}">{fault_label}</div>
            <div style="font-family:var(--mono);font-size:.62rem;color:var(--text4);
                        background:var(--bg3);border:1px solid var(--border);
                        border-radius:4px;padding:3px 8px;">
                {now_str}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPI ROW ───────────────────────────────────────────────────────────────
    w   = snap.get("wind_speed_ms",   0) if snap else 0
    p   = snap.get("power_kw",        0) if snap else 0
    r   = snap.get("rotor_rpm",       0) if snap else 0
    pi  = snap.get("pitch_actual_deg",0) if snap else 0
    ya  = snap.get("yaw_actual_deg",  0) if snap else 0
    gb  = snap.get("temp_gearbox_c",  0) if snap else 0
    gen = snap.get("temp_generator_c",0) if snap else 0
    vib = snap.get("gearbox_vibration_g",0) if snap else 0

    k1,k2,k3,k4,k5,k6,k7,k8 = st.columns(8)
    with k1: st.metric("WIND SPEED",  f"{w:.1f} m/s",   f"{w-9.8:+.1f} vs avg")
    with k2: st.metric("POWER",       f"{p:.0f} kW",    f"{p/3600*100:.0f}% rated")
    with k3: st.metric("ROTOR RPM",   f"{r:.1f}")
    with k4: st.metric("PITCH",       f"{pi:.1f} deg")
    with k5: st.metric("YAW",         f"{ya:.0f} deg")
    with k6: st.metric("GEARBOX T",   f"{gb:.1f} C",    "HIGH" if gb>70 else "NOM")
    with k7: st.metric("GEN T",       f"{gen:.1f} C")
    with k8: st.metric("GBX VIB",     f"{vib:.3f} g",   "WARN" if vib > 0.25 else "NOM")

    st.divider()

    # ── TABS ──────────────────────────────────────────────────────────────────
    t1,t2,t3,t4,t5,t6 = st.tabs([
        "POWER & WIND",
        "THERMAL",
        "SATELLITE LINK",
        "INTERACTIONS",
        "SECURITY",
        "SYSTEM LOG",
    ])

    # ═══ TAB 1: POWER & WIND ══════════════════════════════════════════════════
    with t1:
        main_col, side_col = st.columns([3, 1])

        with main_col:
            st.markdown('<div class="sys-label">Power Output — Real-Time</div>',
                        unsafe_allow_html=True)
            if len(hist["power"]) > 2:
                st.plotly_chart(
                    sparkline(hist["time"], {"POWER (kW)": hist["power"]},
                              height=160, colors=["#1A56DB"],
                              reflines=[(3600,"RATED 3.6 MW","#B45309")]),
                    use_container_width=True, config={"displayModeBar": False}
                )
            else:
                st.markdown('<div class="scada-info">Waiting for live data...</div>',
                            unsafe_allow_html=True)

            st.markdown('<div class="sys-label" style="margin-top:8px">Wind Speed — m/s</div>',
                        unsafe_allow_html=True)
            if len(hist["wind"]) > 2:
                st.plotly_chart(
                    sparkline(hist["time"], {"WIND (m/s)": hist["wind"]},
                              height=140, colors=["#0891B2"],
                              reflines=[(9.8,"MEAN 9.8","#9CA3AF"),
                                        (27.0,"CUT-OUT 27","#DC2626")]),
                    use_container_width=True, config={"displayModeBar": False}
                )

            if len(hist["pitch"]) > 2:
                ph1, ph2 = st.columns(2)
                with ph1:
                    st.markdown('<div class="sys-label">Pitch History</div>',
                                unsafe_allow_html=True)
                    st.plotly_chart(
                        sparkline(hist["time"], {"PITCH (deg)": hist["pitch"]},
                                  height=110, colors=["#B45309"]),
                        use_container_width=True, config={"displayModeBar":False}
                    )
                with ph2:
                    st.markdown('<div class="sys-label">RPM History</div>',
                                unsafe_allow_html=True)
                    st.plotly_chart(
                        sparkline(hist["time"], {"RPM": hist["rpm"]},
                                  height=110, colors=["#7C3AED"]),
                        use_container_width=True, config={"displayModeBar":False}
                    )

        with side_col:
            st.markdown('<div class="sys-label">Wind Bearing</div>',
                        unsafe_allow_html=True)
            wd = snap.get("wind_dir_deg", 225) if snap else 225
            st.plotly_chart(wind_compass(wd, w), use_container_width=True,
                            config={"displayModeBar": False})
            st.markdown(f"""
            <div style="text-align:center;font-family:var(--mono);
                        font-size:.62rem;color:var(--text3);margin-top:-8px;">
                {wd:.0f} deg &nbsp;·&nbsp; {w:.1f} m/s
            </div>
            """, unsafe_allow_html=True)

            st.markdown('<div class="sys-label" style="margin-top:14px">Rotor Speed</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(gauge(r, 22, "RPM", "#0891B2"),
                            use_container_width=True, config={"displayModeBar": False})

            st.markdown('<div class="sys-label">Pitch Angle</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(gauge(pi, 90, "DEGREES", "#B45309"),
                            use_container_width=True, config={"displayModeBar": False})

            power_factor = f"{snap.get('power_factor', 0.95):.2f}" if snap else "—"
            gen_rpm      = f"{snap.get('gen_rpm', 0):.0f}" if snap else "—"
            op_hours     = f"{snap.get('op_hours', 0):.0f}" if snap else "—"

            st.markdown(f"""
            <div class="scada-info" style="margin-top:8px;">
                POWER FACTOR: {power_factor}<br>
                GEN RPM: {gen_rpm}<br>
                OP HOURS: {op_hours} h
            </div>
            """, unsafe_allow_html=True)

    # ═══ TAB 2: THERMAL ═══════════════════════════════════════════════════════
    with t2:
        if snap:
            gc1,gc2,gc3,gc4 = st.columns(4)
            temps = [
                (gc1, "temp_gearbox_c",      100, "GEARBOX",   "#1A56DB", 70),
                (gc2, "temp_generator_c",    120, "GENERATOR", "#0891B2", 85),
                (gc3, "temp_main_bearing_c",  80, "BEARING",   "#B45309", 60),
                (gc4, "temp_nacelle_c",        60, "NACELLE",   "#7C3AED", 45),
            ]
            for col, key, mx, lbl, clr, warn in temps:
                with col:
                    val = snap.get(key, 0)
                    c_use = "#DC2626" if val > warn else clr
                    st.metric(lbl, f"{val:.1f} C",
                              "HIGH" if val > warn else "NOMINAL")
                    st.plotly_chart(gauge(val, mx, f"{lbl} deg C", c_use, height=120),
                                    use_container_width=True,
                                    config={"displayModeBar": False})

        if len(hist["temp_gearbox"]) > 2:
            st.markdown('<div class="sys-label" style="margin-top:8px">Thermal History</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(
                sparkline(hist["time"],
                          {"GEARBOX": hist["temp_gearbox"],
                           "GENERATOR": hist["temp_gen"],
                           "BEARING": hist["temp_bearing"]},
                          height=180,
                          colors=["#1A56DB","#0891B2","#B45309"]),
                use_container_width=True, config={"displayModeBar": False}
            )

        if snap:
            st.markdown('<div class="sys-label">Condition Monitoring</div>',
                        unsafe_allow_html=True)
            m1,m2,m3,m4 = st.columns(4)
            with m1: st.metric("GEARBOX VIB",
                                f"{snap.get('gearbox_vibration_g',0):.3f} g-rms",
                                "WARN" if snap.get("gearbox_vibration_g",0)>0.25 else "NOM")
            with m2: st.metric("BLADE VIB 1P",
                                f"{snap.get('blade_vibration_hz',0):.3f} Hz")
            with m3: st.metric("TOWER FORE-AFT",
                                f"{snap.get('tower_fore_aft_mm',0):.1f} mm")
            with m4: st.metric("BEARING GREASE",
                                f"{snap.get('bearing_grease_temp_c',0):.1f} C")

            if len(hist["vib_gbx"]) > 2:
                st.plotly_chart(
                    sparkline(hist["time"],
                              {"GBX VIB (g-rms)": hist["vib_gbx"]},
                              height=100, colors=["#DC2626"],
                              reflines=[(0.25,"WARN","#B45309")]),
                    use_container_width=True, config={"displayModeBar": False}
                )

    # ═══ TAB 3: SATELLITE LINK ════════════════════════════════════════════════
    with t3:
        el  = ls.get("elevation_deg", 0)
        vis = ls.get("satellite_visible", False)

        orb_col, stat_col = st.columns([1, 2])

        with orb_col:
            st.markdown('<div class="sys-label">Orbital Geometry</div>',
                        unsafe_allow_html=True)
            st.plotly_chart(orbit_vis(el, vis), use_container_width=True,
                            config={"displayModeBar": False})
            link_cls  = "status-online" if vis else "status-warn"
            link_text = "KU-BAND LINK UP" if vis else "SATELLITE BELOW HORIZON"
            st.markdown(f"""
            <div style="text-align:center;margin-top:-4px;padding:8px;">
                <div class="{link_cls}" style="justify-content:center;">
                    {link_text}
                </div>
                <div style="font-family:var(--mono);font-size:.56rem;
                            color:var(--text4);margin-top:6px;">
                    GATEWAY: {GATEWAY_HOST}
                </div>
            </div>
            """, unsafe_allow_html=True)

        with stat_col:
            st.markdown('<div class="sys-label">KU-Band Link Telemetry — from Gateway</div>',
                        unsafe_allow_html=True)
            if ls:
                r1,r2,r3 = st.columns(3)
                with r1:
                    st.metric("ELEVATION",   f"{ls.get('elevation_deg',0):.1f} deg")
                    st.metric("SLANT RANGE", f"{ls.get('slant_range_km',0):.0f} km")
                with r2:
                    st.metric("PROP DELAY",  f"{ls.get('propagation_delay_ms',0):.2f} ms")
                    st.metric("PATH LOSS",   f"{ls.get('path_loss_db',0):.1f} dB")
                with r3:
                    st.metric("DOPPLER",     f"{ls.get('doppler_hz',0):.0f} Hz")
                    st.metric("RAIN ATTEN",  f"{ls.get('rain_attenuation_db',0):.2f} dB")
                st.divider()
                r4,r5,r6 = st.columns(3)
                with r4: st.metric("SEA STATE",  ls.get("sea_description","—"))
                with r5: st.metric("WAVE Hs",    f"{ls.get('wave_height_m',0):.1f} m")
                with r6: st.metric("PKT ERROR",  f"{ls.get('packet_error_rate',0)*100:.2f}%")
                if ls.get("next_aos_s"):
                    st.markdown(f'<div class="scada-warn">NEXT AOS IN {ls["next_aos_s"]:.0f}s SIMULATED TIME</div>',
                                unsafe_allow_html=True)
                if ls.get("pass_remaining_s"):
                    st.markdown(f'<div class="scada-ok">PASS IN PROGRESS — {ls["pass_remaining_s"]:.0f}s REMAINING</div>',
                                unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="scada-alert">NO LINK STATUS FROM GATEWAY — ENSURE {GATEWAY_HOST} IS RUNNING leo_gateway.py</div>',
                            unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="sys-label">Channel Specification</div>',
                    unsafe_allow_html=True)
        sp1,sp2,sp3,sp4 = st.columns(4)
        specs = [
            (sp1, "ORBIT",     "ALT: 600 km LEO\nPERIOD: 96.7 min\nVELOCITY: 7,558 m/s"),
            (sp2, "RF LINK",   "CARRIER: 14.5 GHz\nFSPL: 184.7 dB\nMIN EL: 5 deg"),
            (sp3, "IRISH SEA", f"52.47N 5.56W\nDEPTH: 3-8 m\nSEA: {ls.get('sea_description','Slight')}"),
            (sp4, "CHANNEL",   f"SCINT: {'ACTIVE' if ls.get('scintillation') else 'NONE'}\nOUTAGE: {'YES' if ls.get('forced_outage') else 'NO'}\nLINK: {'UP' if ls.get('link_up') else 'DOWN'}"),
        ]
        for col, hdr, body in specs:
            with col:
                st.markdown(f"""
                <div class="scada-info">
                    <div style="color:var(--teal);font-size:.58rem;
                                letter-spacing:.15em;margin-bottom:5px;font-weight:500;">
                        {hdr}
                    </div>
                    {"<br>".join(body.split(chr(10)))}
                </div>
                """, unsafe_allow_html=True)

    # ═══ TAB 4: INTERACTIONS ══════════════════════════════════════════════════
    with t4:
        st.markdown(f"""
        <div class="scada-info" style="margin-bottom:18px;">
            ALL INTERACTIONS USE REAL NETWORK SOCKETS
            &nbsp;·&nbsp; TURBINE: {TURBINE_HOST}
            &nbsp;·&nbsp; GATEWAY: {GATEWAY_HOST}
        </div>
        """, unsafe_allow_html=True)

        i1,i2 = st.columns(2)

        with i1:
            st.markdown(f"""
            <div style="font-family:var(--mono);font-size:.55rem;letter-spacing:.18em;
                        color:var(--teal);margin-bottom:3px;font-weight:500;">
                INTERACTION 01
            </div>
            <div style="font-family:var(--display);font-size:.95rem;font-weight:700;
                        color:var(--text);margin-bottom:2px;">
                Sensor Poll — TCP Port 9001
            </div>
            <div style="font-family:var(--mono);font-size:.6rem;
                        color:var(--text3);margin-bottom:12px;">
                SENSOR_POLL to TURBINE to SENSOR_REPLY
            </div>
            """, unsafe_allow_html=True)
            if st.button("EXECUTE SENSOR POLL", use_container_width=True, key="i1"):
                t0 = time.time(); data = poll_sensors(); rtt = (time.time()-t0)*1000
                if data:
                    add_log(f"SENSOR_REPLY {rtt:.0f}ms · {data['wind_speed_ms']:.2f}m/s · {data['power_kw']:.0f}kW", "ok")
                    st.markdown(f'<div class="scada-ok">RECEIVED FROM {TURBINE_HOST}:9001 IN {rtt:.0f}ms</div>', unsafe_allow_html=True)
                    cols = st.columns(2)
                    keys_a = ["wind_speed_ms","power_kw","rotor_rpm","pitch_actual_deg"]
                    keys_b = ["yaw_actual_deg","temp_gearbox_c","fault_code","op_hours"]
                    for col, keys in [(cols[0],keys_a),(cols[1],keys_b)]:
                        with col:
                            for k in keys:
                                v = data.get(k,"—")
                                st.markdown(f"""
                                <div style="font-family:var(--mono);font-size:.62rem;
                                            color:var(--text3);padding:3px 0;
                                            border-bottom:1px solid var(--border);">
                                    {k.upper()}
                                    <span style="color:var(--blue);float:right;">{v}</span>
                                </div>""", unsafe_allow_html=True)
                else:
                    add_log(f"SENSOR_POLL FAILED — {TURBINE_HOST}:9001", "err")
                    st.markdown(f'<div class="scada-alert">NO RESPONSE FROM {TURBINE_HOST}:9001</div>', unsafe_allow_html=True)

        with i2:
            st.markdown(f"""
            <div style="font-family:var(--mono);font-size:.55rem;letter-spacing:.18em;
                        color:var(--teal);margin-bottom:3px;font-weight:500;">
                INTERACTION 02
            </div>
            <div style="font-family:var(--display);font-size:.95rem;font-weight:700;
                        color:var(--text);margin-bottom:2px;">
                Pitch Control — TCP via Gateway
            </div>
            <div style="font-family:var(--mono);font-size:.6rem;
                        color:var(--text3);margin-bottom:12px;">
                CMD_PITCH to GATEWAY:9010 to TURBINE:9002 to CONFIRM
            </div>
            """, unsafe_allow_html=True)
            pd = st.selectbox("Pitch angle (deg)", [4.0,15.0,40.0,80.0,90.0],
                              key="i2p",
                              format_func=lambda x: f"{x}deg — {['Optimal','Derate','High wind','Near-feather','Full feather'][int([4.0,15.0,40.0,80.0,90.0].index(x))]}")
            if st.button("SEND VIA SATELLITE GW", use_container_width=True, key="i2"):
                t0 = time.time(); ok, why = send_pitch_gateway(pd); rtt = (time.time()-t0)*1000
                if ok:
                    add_log(f"CMD_PITCH {pd}deg via GW -> ACK {rtt:.0f}ms", "ok")
                    st.markdown(f'<div class="scada-ok">RELAYED: {GATEWAY_HOST}:9010 to {TURBINE_HOST}:9002 IN {rtt:.0f}ms</div>', unsafe_allow_html=True)
                else:
                    add_log(f"CMD_PITCH {pd}deg via GW -> {why}", "warn")
                    st.markdown(f'<div class="scada-warn">GATEWAY: {why}<br>Inject satellite pass if link is down</div>', unsafe_allow_html=True)

        st.divider()
        i3,i4 = st.columns(2)

        with i3:
            st.markdown(f"""
            <div style="font-family:var(--mono);font-size:.55rem;letter-spacing:.18em;
                        color:var(--teal);margin-bottom:3px;font-weight:500;">
                INTERACTION 03
            </div>
            <div style="font-family:var(--display);font-size:.95rem;font-weight:700;
                        color:var(--text);margin-bottom:2px;">
                Yaw Control — UDP Port 9003
            </div>
            <div style="font-family:var(--mono);font-size:.6rem;
                        color:var(--text3);margin-bottom:12px;">
                CMD_YAW DATAGRAM to TURBINE (CONNECTIONLESS)
            </div>
            """, unsafe_allow_html=True)
            yd = st.selectbox("Bearing (deg)", [225.0,240.0,210.0,270.0,180.0],
                              key="i3y",
                              format_func=lambda x: f"{x:.0f}deg ({['SW Prevailing','WSW','SSW','W','S'][int([225,240,210,270,180].index(int(x)))]})")
            if st.button("FIRE UDP DATAGRAM", use_container_width=True, key="i3"):
                actual = send_yaw_udp(yd)
                if actual is not None:
                    add_log(f"CMD_YAW {yd:.0f}deg UDP -> actual {actual}deg", "ok")
                    st.markdown(f'<div class="scada-ok">UDP to {TURBINE_HOST}:9003 — ACTUAL: {actual}deg</div>', unsafe_allow_html=True)
                else:
                    add_log(f"CMD_YAW {yd:.0f}deg UDP sent (no reply)", "info")
                    st.markdown(f'<div class="scada-info">UDP DATAGRAM SENT TO {TURBINE_HOST}:9003<br>No reply required — fire-and-forget</div>', unsafe_allow_html=True)

        with i4:
            st.markdown(f"""
            <div style="font-family:var(--mono);font-size:.55rem;letter-spacing:.18em;
                        color:var(--teal);margin-bottom:3px;font-weight:500;">
                INTERACTION 04
            </div>
            <div style="font-family:var(--display);font-size:.95rem;font-weight:700;
                        color:var(--text);margin-bottom:2px;">
                Telemetry Stream — TCP Port 9004
            </div>
            <div style="font-family:var(--mono);font-size:.6rem;
                        color:var(--text3);margin-bottom:12px;">
                TELEM_START to CONFIRM to TELEM_FRAME x N at 2 Hz
            </div>
            """, unsafe_allow_html=True)
            if st.button("SUBSCRIBE TELEMETRY 4s", use_container_width=True, key="i4"):
                with st.spinner("Receiving telemetry stream..."):
                    frames = subscribe_telemetry_burst(4.0)
                if frames:
                    pwr = [f.get("power_kw",0) for f in frames]
                    wnd = [f.get("wind_speed_ms",0) for f in frames]
                    add_log(f"TELEM {len(frames)} frames · avg {sum(pwr)/len(pwr):.0f}kW", "ok")
                    st.markdown(f'<div class="scada-ok">{len(frames)} FRAMES FROM {TURBINE_HOST}:9004 AT 2Hz</div>', unsafe_allow_html=True)
                    tc1,tc2,tc3 = st.columns(3)
                    with tc1: st.metric("FRAMES",    len(frames))
                    with tc2: st.metric("AVG POWER", f"{sum(pwr)/len(pwr):.0f} kW")
                    with tc3: st.metric("AVG WIND",  f"{sum(wnd)/len(wnd):.1f} m/s")
                else:
                    add_log(f"TELEM failed — {TURBINE_HOST}:9004", "err")
                    st.markdown(f'<div class="scada-alert">NO TELEMETRY FROM {TURBINE_HOST}:9004</div>', unsafe_allow_html=True)

        st.divider()
        st.markdown('<div class="sys-label">Bonus — Discovery / Negotiation / Agreement</div>',
                    unsafe_allow_html=True)
        b1,b2,b3 = st.columns(3)
        with b1:
            if st.button("DISCOVER TURBINES", use_container_width=True, key="disc"):
                nodes = discover_nodes()
                if nodes:
                    for n in nodes: add_log(f"ANNOUNCE_REPLY: {n.get('node_id')} @ {n.get('_ip')}", "ok")
                    st.markdown(f'<div class="scada-ok">FOUND {len(nodes)} NODE(S)</div>', unsafe_allow_html=True)
                    for n in nodes:
                        st.markdown(f"""
                        <div class="scada-info">
                            ID: {n.get('node_id')}<br>
                            MAKE: {n.get('turbine_make','—')}<br>
                            FARM: {n.get('farm','—')}<br>
                            IP: {n.get('_ip','—')}
                        </div>""", unsafe_allow_html=True)
                else:
                    st.markdown('<div class="scada-warn">NO TURBINES DISCOVERED</div>', unsafe_allow_html=True)
        with b2:
            if st.button("NEGOTIATE", use_container_width=True, key="neg"):
                caps = negotiate()
                if caps:
                    add_log(f"OFFER_REPLY: {caps.get('available_services')}", "ok")
                    st.markdown('<div class="scada-ok">NEGOTIATION COMPLETE</div>', unsafe_allow_html=True)
                    for k, v in caps.items():
                        if k != "constraints":
                            st.markdown(f"""
                            <div style="font-family:var(--mono);font-size:.62rem;
                                        color:var(--text3);padding:3px 0;
                                        border-bottom:1px solid var(--border);">
                                {k.upper()}
                                <span style="color:var(--blue);float:right;">{v}</span>
                            </div>""", unsafe_allow_html=True)
                else:
                    st.markdown('<div class="scada-alert">NEGOTIATION FAILED</div>', unsafe_allow_html=True)
        with b3:
            if st.button("COMMIT SESSION", use_container_width=True, key="com"):
                sid = commit_session()
                if sid:
                    add_log(f"COMMIT_ACK: session {sid} active", "ok")
                    st.markdown(f'<div class="scada-ok">SESSION COMMITTED<br>ID: {sid}</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="scada-alert">COMMIT FAILED</div>', unsafe_allow_html=True)

    # ═══ TAB 5: SECURITY ══════════════════════════════════════════════════════
    with t5:
        from security.intrusion_detector import SecurityMonitor

        sc1, sc2 = st.columns([1, 1])
        with sc1:
            st.markdown('<div class="sys-label">Intrusion Detection — Live Simulation</div>',
                        unsafe_allow_html=True)
            atk = st.selectbox("Attack scenario", [
                "Normal pitch (12 deg) — SHOULD ALLOW",
                "Out-of-range pitch (95 deg) — SHOULD BLOCK",
                "Unauthorized ESTOP — SHOULD BLOCK",
                "Replay attack — SHOULD BLOCK",
                "Flood attack (60 msgs) — SHOULD RATE-LIMIT",
                "Pitch rate anomaly — SHOULD FLAG",
            ], key="atk_sel")

            if st.button("EXECUTE ATTACK TEST", use_container_width=True, key="atk_btn"):
                mon = SecurityMonitor()
                mon.trust("ARKLOW_CTRL_1"); mon.trust("ARKLOW_GE3600_T1")
                def mk(org, knd, bdy):
                    return build_msg(knd, org, "ARKLOW_GE3600_T1", bdy)

                if "Normal" in atk:
                    ok, r = mon.inspect(mk("ARKLOW_CTRL_1", MsgKind.CMD_PITCH, {"pitch_deg":12.0}))
                elif "range" in atk:
                    ok, r = mon.inspect(mk("ATTACKER_NODE", MsgKind.CMD_PITCH, {"pitch_deg":95.0}))
                elif "ESTOP" in atk:
                    ok, r = mon.inspect(mk("ROGUE_NODE", MsgKind.CMD_ESTOP, {}))
                elif "Replay" in atk:
                    m = mk("ARKLOW_CTRL_1", MsgKind.CMD_PITCH, {"pitch_deg":8.0})
                    mon.inspect(m); ok, r = mon.inspect(m)
                elif "Flood" in atk:
                    al = sum(1 for i in range(60) if mon.inspect({
                        **mk("FLOOD_BOT", MsgKind.SENSOR_POLL, {}),
                        "mid": str(uuid.uuid4()), "seq": i})[0])
                    ok, r = False, f"{al}/60 ALLOWED — REST RATE-LIMITED"
                elif "rate" in atk.lower():
                    m1 = mk("FAST_ATK", MsgKind.CMD_PITCH, {"pitch_deg":10.0})
                    mon.inspect(m1); time.sleep(0.01)
                    ok, r = mon.inspect({**mk("FAST_ATK", MsgKind.CMD_PITCH, {"pitch_deg":20.0}), "mid": str(uuid.uuid4())})

                res_cls  = "scada-ok"    if ok else "scada-alert"
                res_icon = "ALLOW"       if ok else "BLOCK"
                st.markdown(f'<div class="{res_cls}"><b>{res_icon}</b> — {r}</div>',
                            unsafe_allow_html=True)
                add_log(f"IDS: {res_icon} — {r}", "ok" if ok else "err")

        with sc2:
            st.markdown('<div class="sys-label">Security Policy — ABWCP/1.0</div>',
                        unsafe_allow_html=True)
            rules = [
                ("RATE LIMITER",   "Token bucket · 20 msg/s per source · burst 40"),
                ("REPLAY CACHE",   "5-minute msg_id deduplication window"),
                ("COMMAND BOUNDS", "Pitch 0–90 deg (GE 3.6-100 physical limits)"),
                ("PITCH RATE",     "Reject cmds arriving less than 100ms apart"),
                ("UNAUTH ESTOP",   "Emergency stop from unregistered nodes blocked"),
                ("SEQ INJECTION",  "Flag sequence number jumps greater than 1000"),
            ]
            for name, detail in rules:
                st.markdown(f"""
                <div class="scada-info">
                    <span style="color:var(--blue);letter-spacing:.08em;
                                 font-weight:500;">{name}</span><br>
                    {detail}
                </div>
                """, unsafe_allow_html=True)

    # ═══ TAB 6: SYSTEM LOG ════════════════════════════════════════════════════
    with t6:
        st.markdown('<div class="sys-label">System Event Log</div>',
                    unsafe_allow_html=True)

        lb1, lb2, lb3 = st.columns(3)
        with lb1:
            if st.button("DISCOVER NODES", use_container_width=True, key="log_disc"):
                nodes = discover_nodes()
                for n in nodes:
                    add_log(f"ANNOUNCE_REPLY: {n.get('node_id')} @ {n.get('_ip')}", "ok")
                if not nodes: add_log("NO NODES DISCOVERED", "warn")
        with lb2:
            if st.button("FETCH TELEMETRY 5s", use_container_width=True, key="log_telem"):
                def _t():
                    frames = subscribe_telemetry_burst(5.0)
                    if frames:
                        pwr = [f.get("power_kw",0) for f in frames]
                        add_log(f"TELEM BURST: {len(frames)} frames · avg {sum(pwr)/len(pwr):.0f}kW", "ok")
                    else:
                        add_log("TELEM BURST: no frames received", "err")
                threading.Thread(target=_t, daemon=True).start()
                add_log("TELEM SUBSCRIPTION STARTED (5s)...", "info")
        with lb3:
            if st.button("CLEAR LOG", use_container_width=True, key="clr"):
                log.clear()

        st.divider()

        if log:
            for entry in log:
                cls = {"ok":"ok","err":"err","warn":"warn"}.get(entry["k"],"")
                st.markdown(
                    f'<div class="log-entry {cls}">'
                    f'<span style="color:var(--text4);margin-right:10px;">[{entry["t"]}]</span>'
                    f'{entry["msg"]}</div>',
                    unsafe_allow_html=True
                )
        else:
            st.markdown(
                '<div class="scada-info">NO EVENTS — use controls to generate log entries</div>',
                unsafe_allow_html=True
            )

    # ── FOOTER ────────────────────────────────────────────────────────────────
    st.markdown(f"""
    <div style="display:flex;align-items:center;justify-content:space-between;
                padding:10px 16px;
                background:var(--bg2);
                border:1px solid var(--border);
                border-radius:8px;
                margin-top:10px;
                font-family:var(--mono);font-size:.53rem;
                color:var(--text4);letter-spacing:.08em;
                box-shadow:var(--shadow-sm);">
        <div>ABWCP/1.0 &nbsp;·&nbsp; 14.5 GHz KU-BAND &nbsp;·&nbsp; 600 KM LEO &nbsp;·&nbsp; ARKLOW BANK WIND PARK</div>
        <div>TURBINE: {TURBINE_HOST} &nbsp;·&nbsp; GATEWAY: {GATEWAY_HOST}</div>
        <div>T1 &nbsp;·&nbsp; {snap.get('op_hours',0) if snap else 0:.0f} OP HOURS &nbsp;·&nbsp; 7 x GE 3.6-100 &nbsp;·&nbsp; 25.2 MW</div>
        <div style="background:var(--bg3);border:1px solid var(--border);
                    border-radius:4px;padding:2px 8px;color:var(--text3);">
            {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>
    """, unsafe_allow_html=True)

   


if __name__ == "__main__":
    main()
