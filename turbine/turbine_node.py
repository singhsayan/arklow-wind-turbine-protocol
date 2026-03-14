"""
turbine_node.py
===============
Arklow Bank GE 3.6-100 — Network Node
======================================
Runs the turbine-side socket servers and autonomous safety controller
for Turbine T1 at Arklow Bank Wind Park.

Six concurrent servers:
  Port 9001  TCP  – Sensor polling server       (Interaction 1)
  Port 9002  TCP  – Blade pitch command server  (Interaction 2)
  Port 9003  UDP  – Nacelle yaw command server  (Interaction 3)
  Port 9004  TCP  – Telemetry subscription server (Interaction 4)
  Port 9005  TCP  – O&M camera stream server    (Bonus)
  Port 9100  UDP  – Node discovery / negotiation (Bonus)

Autonomous controller state machine:
  REMOTE   → normal remote control from space station
  DERATING → high wind: pitch up to reduce load
  SURVIVAL → storm: pitch to 60°, reduce to 20% output
  SHUTDOWN → emergency: feather and halt rotor
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import socket
import threading
import time
import json
import logging
import signal
from enum import Enum

from utils.protocol import (
    build_msg, tcp_send, tcp_recv, make_confirm, make_reject,
    fmt_msg, MsgKind, ALL_NODES, PROTO_VERSION
)
try:
    from turbine.turbine_physics import TurbineSimulator
except ModuleNotFoundError:
    from turbine_physics import TurbineSimulator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ArklowTurbineNode")

# ── Node identity ─────────────────────────────────────────────────────────────
NODE_ID         = "ARKLOW_GE3600_T1"
CTRL_STATION_ID = "ARKLOW_CTRL_1"

# ── Network config ────────────────────────────────────────────────────────────
BIND_ADDR       = "0.0.0.0"
PORT_SENSOR     = 9001
PORT_PITCH      = 9002
PORT_YAW        = 9003   # UDP
PORT_TELEMETRY  = 9004
PORT_CAMERA     = 9005
PORT_DISCOVERY  = 9100   # UDP

TELEM_HZ        = 2       # telemetry push rate
MAX_CONN        = 8
COMMS_TIMEOUT_S = 30.0    # seconds of silence before autonomous mode


# ── Autonomous controller states ──────────────────────────────────────────────

class CtrlState(Enum):
    REMOTE   = "REMOTE"     # space station in control
    DERATING = "DERATING"   # high wind — partial feather
    SURVIVAL = "SURVIVAL"   # storm — survival mode
    SHUTDOWN = "SHUTDOWN"   # emergency stop


# ── Turbine network node ──────────────────────────────────────────────────────

class ArklowTurbineNode:
    """
    Network node for one GE 3.6-100 turbine at Arklow Bank.
    Runs all socket servers and the autonomous safety controller.
    """

    def __init__(self):
        self._sim            = TurbineSimulator(NODE_ID)
        self._active         = True
        self._last_rx_time   = time.time()
        self._ctrl_state     = CtrlState.REMOTE
        self._telem_clients: dict[str, tuple] = {}  # key → (socket, node_id)
        self._telem_lock     = threading.Lock()

    # ── Start / stop ──────────────────────────────────────────────────────────

    def start(self):
        self._sim.start()
        logger.info(f"╔══ {NODE_ID} ══╗")
        logger.info(f"  Farm     : Arklow Bank Wind Park, Co. Wicklow")
        logger.info(f"  Turbine  : GE 3.6-100  (3.6 MW, 100 m rotor)")
        logger.info(f"  Location : 52.47°N 5.56°W — Irish Sea")
        logger.info(f"  Sensor   : TCP {BIND_ADDR}:{PORT_SENSOR}")
        logger.info(f"  Pitch    : TCP {BIND_ADDR}:{PORT_PITCH}")
        logger.info(f"  Yaw      : UDP {BIND_ADDR}:{PORT_YAW}")
        logger.info(f"  Telemetry: TCP {BIND_ADDR}:{PORT_TELEMETRY}")
        logger.info(f"  Camera   : TCP {BIND_ADDR}:{PORT_CAMERA}")
        logger.info(f"  Discovery: UDP {BIND_ADDR}:{PORT_DISCOVERY}")

        workers = [
            ("SensorSrv",    self._sensor_server),
            ("PitchSrv",     self._pitch_server),
            ("YawSrv",       self._yaw_server),
            ("TelemSrv",     self._telem_server),
            ("TelemPush",    self._telem_push_loop),
            ("CameraSrv",    self._camera_server),
            ("DiscoverySrv", self._discovery_server),
            ("AutoCtrl",     self._autonomous_controller),
        ]
        for name, fn in workers:
            threading.Thread(target=fn, name=name, daemon=True).start()
        logger.info("All servers running.")

    def stop(self):
        self._active = False

    # ── Interaction 1: Sensor polling server (TCP 9001) ───────────────────────

    def _sensor_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((BIND_ADDR, PORT_SENSOR))
            srv.listen(MAX_CONN)
            srv.settimeout(1.0)
            logger.info(f"[SensorSrv] TCP {PORT_SENSOR} ready")
            while self._active:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_sensor_conn,
                    args=(conn, addr), daemon=True
                ).start()

    def _handle_sensor_conn(self, conn, addr):
        peer = f"{addr[0]}:{addr[1]}"
        with conn:
            conn.settimeout(15.0)
            while self._active:
                msg = tcp_recv(conn)
                if msg is None:
                    break
                self._last_rx_time = time.time()

                if msg["kind"] == MsgKind.SENSOR_POLL:
                    snap = self._sim.sensor_snapshot()
                    reply = build_msg(MsgKind.SENSOR_REPLY,
                                      NODE_ID, msg["origin"], snap)
                    tcp_send(conn, reply)

                elif msg["kind"] == MsgKind.PING:
                    pong = build_msg(MsgKind.PONG, NODE_ID, msg["origin"],
                                     {"uptime_h": round(
                                         self._sim.get_state().op_hours, 1)})
                    tcp_send(conn, pong)

                else:
                    tcp_send(conn, make_reject(msg, NODE_ID,
                                               reason="WRONG_PORT"))

    # ── Interaction 2: Pitch command server (TCP 9002) ────────────────────────

    def _pitch_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((BIND_ADDR, PORT_PITCH))
            srv.listen(MAX_CONN)
            srv.settimeout(1.0)
            logger.info(f"[PitchSrv] TCP {PORT_PITCH} ready")
            while self._active:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_pitch_conn,
                    args=(conn, addr), daemon=True
                ).start()

    def _handle_pitch_conn(self, conn, addr):
        with conn:
            conn.settimeout(30.0)
            while self._active:
                msg = tcp_recv(conn)
                if msg is None:
                    break
                logger.info(f"[PitchSrv] {fmt_msg(msg)}")
                self._last_rx_time = time.time()

                if msg["kind"] == MsgKind.CMD_PITCH:
                    angle = float(msg["body"].get("pitch_deg", 5.0))
                    ok    = self._sim.cmd_pitch(angle)
                    snap  = self._sim.sensor_snapshot()
                    reply = make_confirm(msg, NODE_ID, extra={
                        "pitch_demand_deg": snap["pitch_demand_deg"],
                        "pitch_actual_deg": snap["pitch_actual_deg"],
                        "accepted":         ok,
                    })
                    tcp_send(conn, reply)

                elif msg["kind"] == MsgKind.CMD_ESTOP:
                    self._sim.cmd_estop()
                    tcp_send(conn, make_confirm(msg, NODE_ID,
                                                extra={"status": "ESTOP_ACTIVATED"}))

                else:
                    tcp_send(conn, make_reject(msg, NODE_ID, "WRONG_PORT"))

    # ── Interaction 3: Yaw command server (UDP 9003) ──────────────────────────
    # UDP is used deliberately — yaw is a continuous slow adjustment (0.5°/s).
    # A lost datagram has negligible impact as the next arrives within ~1 second.

    def _yaw_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((BIND_ADDR, PORT_YAW))
            srv.settimeout(1.0)
            logger.info(f"[YawSrv] UDP {PORT_YAW} ready")
            while self._active:
                try:
                    data, addr = srv.recvfrom(65535)
                except socket.timeout:
                    continue
                try:
                    msg = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                self._last_rx_time = time.time()

                if msg.get("kind") == MsgKind.CMD_YAW:
                    bearing = float(msg["body"].get("yaw_deg", 225.0))
                    ok      = self._sim.cmd_yaw(bearing)
                    snap    = self._sim.sensor_snapshot()
                    reply   = make_confirm(msg, NODE_ID, extra={
                        "yaw_demand_deg": snap["yaw_demand_deg"],
                        "yaw_actual_deg": snap["yaw_actual_deg"],
                        "accepted":       ok,
                    })
                    srv.sendto(json.dumps(reply).encode(), addr)

                elif msg.get("kind") == MsgKind.SENSOR_POLL:
                    snap  = self._sim.sensor_snapshot()
                    reply = build_msg(MsgKind.SENSOR_REPLY,
                                      NODE_ID, msg.get("origin", "UNKNOWN"), snap)
                    srv.sendto(json.dumps(reply).encode(), addr)

    # ── Interaction 4: Telemetry subscription server (TCP 9004) ──────────────

    def _telem_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((BIND_ADDR, PORT_TELEMETRY))
            srv.listen(MAX_CONN)
            srv.settimeout(1.0)
            logger.info(f"[TelemSrv] TCP {PORT_TELEMETRY} ready")
            while self._active:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_telem_conn,
                    args=(conn, addr), daemon=True
                ).start()

    def _handle_telem_conn(self, conn, addr):
        key  = f"{addr[0]}:{addr[1]}"
        with conn:
            conn.settimeout(10.0)
            msg = tcp_recv(conn)
            if msg is None:
                return
            if msg["kind"] == MsgKind.TELEM_START:
                with self._telem_lock:
                    self._telem_clients[key] = (conn, msg["origin"])
                tcp_send(conn, make_confirm(msg, NODE_ID,
                                            extra={"rate_hz": TELEM_HZ}))
                logger.info(f"[TelemSrv] {msg['origin']} subscribed")
                # Hold connection open; push loop handles sending
                while self._active and key in self._telem_clients:
                    time.sleep(0.5)
            elif msg["kind"] == MsgKind.TELEM_STOP:
                with self._telem_lock:
                    self._telem_clients.pop(key, None)
                tcp_send(conn, make_confirm(msg, NODE_ID))
        with self._telem_lock:
            self._telem_clients.pop(key, None)

    def _telem_push_loop(self):
        """Push telemetry frames to all subscribed clients at TELEM_HZ."""
        interval = 1.0 / TELEM_HZ
        seq      = 0
        while self._active:
            time.sleep(interval)
            snap = self._sim.sensor_snapshot()
            dead = []
            with self._telem_lock:
                for key, (conn, dst_id) in self._telem_clients.items():
                    frame = build_msg(MsgKind.TELEM_FRAME,
                                      NODE_ID, dst_id,
                                      {"seq": seq, "data": snap})
                    if not tcp_send(conn, frame):
                        dead.append(key)
                for k in dead:
                    del self._telem_clients[k]
            seq += 1

    # ── Bonus: Camera stream server (TCP 9005) ────────────────────────────────

    def _camera_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((BIND_ADDR, PORT_CAMERA))
            srv.listen(4)
            srv.settimeout(1.0)
            logger.info(f"[CameraSrv] TCP {PORT_CAMERA} ready")
            while self._active:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_camera_conn,
                    args=(conn, addr), daemon=True
                ).start()

    def _handle_camera_conn(self, conn, addr):
        with conn:
            conn.settimeout(5.0)
            req = tcp_recv(conn)
            if req is None or req["kind"] != MsgKind.CAM_REQUEST:
                return
            fps      = max(1, min(req["body"].get("fps", 2), 5))
            interval = 1.0 / fps
            frame_n  = 0
            while self._active:
                snap  = self._sim.sensor_snapshot()
                frame = _render_turbine_display(snap, frame_n)
                msg   = build_msg(MsgKind.CAM_FRAME, NODE_ID, req["origin"], {
                    "frame_n":  frame_n,
                    "fps":      fps,
                    "encoding": "ASCII",
                    "data":     frame,
                    "captured": time.time(),
                })
                if not tcp_send(conn, msg):
                    break
                frame_n  += 1
                time.sleep(interval)

    # ── Bonus: Discovery / negotiation server (UDP 9100) ──────────────────────

    def _discovery_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            srv.bind((BIND_ADDR, PORT_DISCOVERY))
            srv.settimeout(1.0)
            logger.info(f"[DiscoverySrv] UDP {PORT_DISCOVERY} ready")
            while self._active:
                try:
                    data, addr = srv.recvfrom(65535)
                except socket.timeout:
                    continue
                try:
                    msg = json.loads(data.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                self._handle_discovery_msg(srv, msg, addr)

    def _handle_discovery_msg(self, sock, msg: dict, addr):
        kind = msg.get("kind")

        if kind == MsgKind.ANNOUNCE:
            logger.info(f"[Discovery] ANNOUNCE from {msg.get('origin')} @ {addr}")
            reply = build_msg(MsgKind.ANNOUNCE_REPLY, NODE_ID,
                              msg.get("origin", ALL_NODES), {
                "node_id":      NODE_ID,
                "node_type":    "OFFSHORE_WIND_TURBINE",
                "turbine_make": "GE 3.6-100",
                "farm":         "Arklow Bank Wind Park",
                "location":     {
                    "lat":        52.47,
                    "lon":        -5.56,
                    "country":    "Ireland",
                    "sea":        "Irish Sea",
                    "water_depth_m": 5.0,
                },
                "rated_kw":     3600,
                "rotor_dia_m":  100,
                "capabilities": [
                    "SENSOR_POLL", "CMD_PITCH", "CMD_YAW",
                    "TELEM_STREAM", "CAM_STREAM", "ESTOP"
                ],
                "ports": {
                    "sensor":    PORT_SENSOR,
                    "pitch":     PORT_PITCH,
                    "yaw":       PORT_YAW,
                    "telemetry": PORT_TELEMETRY,
                    "camera":    PORT_CAMERA,
                },
                "protocol":     PROTO_VERSION,
            })
            sock.sendto(json.dumps(reply).encode(), addr)

        elif kind == MsgKind.OFFER:
            logger.info(f"[Discovery] OFFER from {msg.get('origin')}")
            reply = build_msg(MsgKind.OFFER_REPLY, NODE_ID,
                              msg.get("origin", ALL_NODES), {
                "available_services": [
                    "sensor_polling", "pitch_control",
                    "yaw_control", "telemetry_stream", "camera_stream"
                ],
                "telemetry_rate_hz":  TELEM_HZ,
                "data_format":        PROTO_VERSION,
                "constraints": {
                    "pitch_min_deg":    0.0,
                    "pitch_max_deg":    90.0,
                    "pitch_rate_deg_s": 8.0,
                    "yaw_rate_deg_s":   0.5,
                    "rated_power_kw":   3600,
                    "cut_in_ms":        3.5,
                    "cut_out_ms":       27.0,
                },
            })
            sock.sendto(json.dumps(reply).encode(), addr)

        elif kind == MsgKind.COMMIT:
            logger.info(f"[Discovery] COMMIT from {msg.get('origin')}: "
                        f"{msg.get('body',{}).get('session_id','?')}")
            reply = build_msg(MsgKind.COMMIT_ACK, NODE_ID,
                              msg.get("origin", ALL_NODES), {
                "status":     "COMMITTED",
                "session_id": msg.get("body", {}).get("session_id"),
            })
            sock.sendto(json.dumps(reply).encode(), addr)

    # ── Autonomous safety controller (state machine) ──────────────────────────

    def _autonomous_controller(self):
        """
        State-machine safety controller active during satellite blackouts.
        Transitions:
          REMOTE   → DERATING   : no comms AND wind > 18 m/s
          REMOTE   → SURVIVAL   : no comms AND wind > 23 m/s
          REMOTE   → SHUTDOWN   : no comms AND wind > 27 m/s (cut-out)
          DERATING → REMOTE     : comms restored
          SURVIVAL → REMOTE     : comms restored
          SHUTDOWN → REMOTE     : comms restored (after clear)
          Any      → REMOTE     : comms restored
        Hysteresis: state only changes after 2 consecutive checks (10 s).
        """
        logger.info("[AutoCtrl] Autonomous safety controller armed")
        prev_state = CtrlState.REMOTE
        trigger_count = 0

        while self._active:
            time.sleep(5.0)
            now       = time.time()
            comms_gap = now - self._last_rx_time
            snap      = self._sim.sensor_snapshot()
            wind      = snap["wind_speed_ms"]

            if comms_gap <= COMMS_TIMEOUT_S:
                # Comms alive — return to remote if we were autonomous
                if self._ctrl_state != CtrlState.REMOTE:
                    logger.info(
                        f"[AutoCtrl] Comms restored after {comms_gap:.0f}s "
                        f"→ returning to REMOTE"
                    )
                self._ctrl_state = CtrlState.REMOTE
                trigger_count    = 0
                continue

            # Comms lost — determine target state based on wind
            if wind >= CUT_OUT_MS:
                target = CtrlState.SHUTDOWN
            elif wind >= 23.0:
                target = CtrlState.SURVIVAL
            elif wind >= 18.0:
                target = CtrlState.DERATING
            else:
                target = CtrlState.REMOTE   # calm — hold last pitch

            # Hysteresis: require 2 consecutive triggers before changing
            if target != self._ctrl_state:
                trigger_count += 1
                if trigger_count >= 2:
                    self._ctrl_state = target
                    trigger_count    = 0
            else:
                trigger_count = 0

            # Apply actions for current state
            if self._ctrl_state == CtrlState.REMOTE:
                # Mild wind, no comms — hold optimal pitch
                self._sim.cmd_pitch(4.0)
                self._sim.cmd_yaw(snap["wind_dir_deg"])
                logger.info(
                    f"[AutoCtrl] No comms ({comms_gap:.0f}s), calm wind "
                    f"({wind:.1f} m/s) — holding optimal"
                )

            elif self._ctrl_state == CtrlState.DERATING:
                # Moderate-high wind — partial feather + curtail
                self._sim.cmd_pitch(15.0)
                self._sim.cmd_curtail(70.0)
                self._sim.cmd_yaw(snap["wind_dir_deg"])
                logger.warning(
                    f"[AutoCtrl] DERATING — wind {wind:.1f} m/s, "
                    f"pitch 15°, curtail 70%"
                )

            elif self._ctrl_state == CtrlState.SURVIVAL:
                # High wind — survival mode
                self._sim.cmd_pitch(55.0)
                self._sim.cmd_curtail(20.0)
                logger.warning(
                    f"[AutoCtrl] SURVIVAL — wind {wind:.1f} m/s, "
                    f"pitch 55°, curtail 20%"
                )

            elif self._ctrl_state == CtrlState.SHUTDOWN:
                self._sim.cmd_estop()
                logger.critical(
                    f"[AutoCtrl] SHUTDOWN — wind {wind:.1f} m/s "
                    f"≥ cut-out {CUT_OUT_MS} m/s → ESTOP"
                )

            prev_state = self._ctrl_state

        logger.info("[AutoCtrl] Controller stopped")


# ── O&M camera display renderer ───────────────────────────────────────────────

def _render_turbine_display(snap: dict, frame_n: int) -> str:
    wind  = snap["wind_speed_ms"]
    power = snap["power_kw"]
    pitch = snap["pitch_actual_deg"]
    yaw   = snap["yaw_actual_deg"]
    rpm   = snap["rotor_rpm"]
    gbx_t = snap["temp_gearbox_c"]
    fault = snap["fault_code"]
    ts    = time.strftime("%H:%M:%S", time.localtime(snap["timestamp"]))
    blade = "╱" if (frame_n // 4) % 2 == 0 else "╲"

    w = 60
    lines = [
        f"┌{'─'*w}┐",
        f"│  ARKLOW BANK WIND PARK — GE 3.6-100  Frame #{frame_n:<5d} {ts:>8s}  │",
        f"│  Turbine: {snap['turbine_id']:<20s}  52.47°N 5.56°W        │",
        f"├{'─'*w}┤",
        f"│  Wind  : {wind:>6.2f} m/s   RPM : {rpm:>5.2f}   Pitch: {pitch:>5.1f}°    │",
        f"│  Power : {power:>7.1f} kW    Yaw : {yaw:>6.1f}°  GBX T: {gbx_t:>5.1f}°C  │",
        f"│  Fault : {fault:<3}  Tower fore-aft: {snap['tower_fore_aft_mm']:>6.2f} mm           │",
        f"├{'─'*w}┤",
        f"│                         {blade}── Blade 1                     │",
        f"│              ┌──────────┤                             │",
        f"│  Irish Sea ~~│  Nacelle │──── Hub ──── {blade}── Blade 2   │",
        f"│           ~~~│__________│                             │",
        f"│              │  Tower   │                             │",
        f"│         ~~~~~│══════════│~~~~~~~~~~~~~~~~~~~~~~~~~~~~~│",
        f"│         Monopile Foundation  (3–8m water depth)      │",
        f"└{'─'*w}┘",
    ]
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

# Expose cut-out speed for autonomous controller reference
try:
    from turbine.turbine_physics import CUT_OUT_MS
except ModuleNotFoundError:
    from turbine_physics import CUT_OUT_MS

if __name__ == "__main__":
    node = ArklowTurbineNode()
    node.start()

    def _on_signal(sig, frame):
        print("\nShutting down Arklow turbine node…")
        node.stop()

    signal.signal(signal.SIGINT, _on_signal)
    while node._active:
        time.sleep(1)