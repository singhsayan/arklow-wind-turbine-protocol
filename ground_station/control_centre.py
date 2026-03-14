"""
control_centre.py
=================
Arklow Bank Space Station Control Centre
=========================================
Controls and monitors the Arklow Bank GE 3.6-100 turbine via
the Ku-band LEO satellite gateway.

All commands are sent through the gateway (port 9010) which applies
realistic channel physics (delay, loss, orbital visibility).
Some interactions connect directly to the turbine for comparison.

Interaction types:
  1. Sensor poll       – SENSOR_POLL → SENSOR_REPLY  (TCP 9001)
  2. Pitch command     – CMD_PITCH   → CONFIRM        (TCP 9002)
  3. Yaw command       – CMD_YAW    (UDP 9003, fire-and-forget)
  4. Telemetry stream  – TELEM_START → TELEM_FRAME ×N (TCP 9004)
  5. Camera stream     – CAM_REQUEST → CAM_FRAME ×N   (TCP 9005, bonus)
  6. Discovery         – ANNOUNCE → ANNOUNCE_REPLY    (UDP 9100, bonus)
  7. Negotiation       – OFFER   → OFFER_REPLY        (UDP 9100, bonus)
  8. Agreement         – COMMIT  → COMMIT_ACK         (UDP 9100, bonus)
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys
import os

import socket
import threading
import time
import json
import uuid
import logging

from utils.protocol import (
    build_msg, tcp_send, tcp_recv, make_confirm,
    fmt_msg, MsgKind, ALL_NODES, PROTO_VERSION
)
from security.intrusion_detector import SecurityMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ArklowCtrlCentre")

STATION_ID     = "ARKLOW_CTRL_1"
TURBINE_ID     = "ARKLOW_GE3600_T1"
GW_HOST        = "127.0.0.1"
GW_PORT        = 9010
TURBINE_HOST   = "127.0.0.1"
LINK_STATUS_PORT = 9012
DISCOVER_PORT  = 9100
CONN_TIMEOUT   = 10.0


class ArklowControlCentre:
    """Space station control centre for Arklow Bank Wind Park."""

    def __init__(self):
        self._security      = SecurityMonitor(alert_cb=self._on_security_event)
        self._security.trust(STATION_ID)
        self._security.trust(TURBINE_ID)
        self._security.trust("ARKLOW_SAT_GW_1")
        self._running       = True
        self._link_status   = {}
        self._telem_frames  = []
        self._event_log     = []

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _gw_socket(self) -> socket.socket:
        """Return a connected TCP socket to the Ku-band gateway."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(CONN_TIMEOUT)
        s.connect((GW_HOST, GW_PORT))
        return s

    def _turbine_socket(self, port: int) -> socket.socket:
        """Return a direct TCP connection to the turbine (bypasses gateway)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(CONN_TIMEOUT)
        s.connect((TURBINE_HOST, port))
        return s

    def _log(self, msg: str):
        ts   = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._event_log.append(line)
        logger.info(line)

    # ── Interaction 1: Sensor poll ────────────────────────────────────────────

    def poll_sensors(self, via_gateway: bool = True) -> dict | None:
        """
        Send SENSOR_POLL and return the turbine sensor snapshot.
        via_gateway=True routes through the Ku-band channel model.
        """
        try:
            sock = self._gw_socket() if via_gateway else self._turbine_socket(9001)
            with sock:
                req = build_msg(MsgKind.SENSOR_POLL, STATION_ID, TURBINE_ID, {})
                self._log(f"→ SENSOR_POLL seq={req['seq']}")
                tcp_send(sock, req)
                resp = tcp_recv(sock)
                if resp is None:
                    self._log("✗ No response (link down or packet lost)")
                    return None
                if resp["kind"] == MsgKind.SENSOR_REPLY:
                    snap = resp["body"]
                    self._log(
                        f"← SENSOR_REPLY  wind={snap.get('wind_speed_ms')} m/s  "
                        f"power={snap.get('power_kw')} kW  "
                        f"rpm={snap.get('rotor_rpm')}"
                    )
                    return snap
                elif resp["kind"] == MsgKind.REJECT:
                    self._log(f"✗ REJECT: {resp['body'].get('reason')}")
                return None
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            self._log(f"✗ poll_sensors failed: {exc}")
            return None

    # ── Interaction 2: Pitch command ──────────────────────────────────────────

    def command_pitch(self, angle_deg: float,
                      via_gateway: bool = True) -> bool:
        """Send CMD_PITCH to the turbine. Returns True if confirmed."""
        if not (0.0 <= angle_deg <= 90.0):
            self._log(f"✗ Pitch {angle_deg}° rejected by local security check")
            return False
        try:
            sock = self._gw_socket() if via_gateway else self._turbine_socket(9002)
            with sock:
                cmd = build_msg(MsgKind.CMD_PITCH, STATION_ID, TURBINE_ID,
                                {"pitch_deg": angle_deg})
                self._log(f"→ CMD_PITCH {angle_deg}°")
                tcp_send(sock, cmd)
                resp = tcp_recv(sock)
                if resp is None:
                    self._log("✗ No confirmation received")
                    return False
                ok = resp["body"].get("accepted", False)
                self._log(
                    f"← {resp['kind']}  accepted={ok}  "
                    f"actual={resp['body'].get('pitch_actual_deg')}°"
                )
                return ok
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            self._log(f"✗ command_pitch failed: {exc}")
            return False

    # ── Interaction 3: Yaw command (UDP) ──────────────────────────────────────

    def command_yaw(self, bearing_deg: float) -> dict | None:
        """
        Send CMD_YAW via UDP directly to the turbine yaw server.
        UDP chosen deliberately: yaw is a continuous slow adjustment (0.5°/s)
        so a lost datagram has negligible impact on turbine operation.
        """
        bearing_deg = float(bearing_deg) % 360.0
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(CONN_TIMEOUT)
                cmd  = build_msg(MsgKind.CMD_YAW, STATION_ID, TURBINE_ID,
                                 {"yaw_deg": bearing_deg})
                data = json.dumps(cmd).encode()
                self._log(f"→ CMD_YAW (UDP) {bearing_deg:.0f}°")
                s.sendto(data, (TURBINE_HOST, 9003))
                try:
                    resp_data, _ = s.recvfrom(65535)
                    resp = json.loads(resp_data.decode())
                    actual = resp.get("body", {}).get("yaw_actual_deg", "?")
                    self._log(f"← YAW CONFIRM (UDP)  actual={actual}°")
                    return resp
                except socket.timeout:
                    self._log("  (no UDP response — fire-and-forget)")
                    return None
        except OSError as exc:
            self._log(f"✗ command_yaw failed: {exc}")
            return None

    # ── Interaction 4: Telemetry subscription ─────────────────────────────────

    def subscribe_telemetry(self, duration_s: float = 10.0):
        """
        Subscribe to turbine telemetry stream.
        Turbine pushes TELEM_FRAME at 2 Hz for duration_s seconds.
        """
        self._log(f"→ TELEM_START (subscribing for {duration_s:.0f}s)")
        try:
            with self._turbine_socket(9004) as s:
                sub = build_msg(MsgKind.TELEM_START, STATION_ID, TURBINE_ID, {})
                tcp_send(s, sub)
                ack = tcp_recv(s)
                if ack is None or ack["kind"] != MsgKind.CONFIRM:
                    self._log("✗ Subscription not confirmed")
                    return
                self._log(
                    f"← CONFIRM  rate={ack['body'].get('rate_hz')} Hz"
                )
                s.settimeout(5.0)
                start = time.time()
                count = 0
                while time.time() - start < duration_s:
                    msg = tcp_recv(s)
                    if msg is None:
                        break
                    if msg["kind"] == MsgKind.TELEM_FRAME:
                        count += 1
                        snap = msg["body"].get("data", {})
                        self._telem_frames.append(snap)
                        if count % 4 == 0:
                            self._log(
                                f"  TELEM #{count:3d}: "
                                f"wind={snap.get('wind_speed_ms'):.2f} m/s  "
                                f"power={snap.get('power_kw'):.1f} kW  "
                                f"gbx={snap.get('temp_gearbox_c'):.1f}°C"
                            )
                self._log(f"← Received {count} telemetry frames in {duration_s:.0f}s")
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            self._log(f"✗ subscribe_telemetry failed: {exc}")

    # ── Bonus: Camera stream ──────────────────────────────────────────────────

    def request_camera(self, n_frames: int = 3):
        """Request O&M camera frames from the turbine."""
        self._log(f"→ CAM_REQUEST (requesting {n_frames} frames @ 2fps)")
        try:
            with self._turbine_socket(9005) as s:
                req = build_msg(MsgKind.CAM_REQUEST, STATION_ID, TURBINE_ID,
                                {"fps": 2})
                tcp_send(s, req)
                s.settimeout(5.0)
                received = 0
                while received < n_frames:
                    msg = tcp_recv(s)
                    if msg is None:
                        break
                    if msg["kind"] == MsgKind.CAM_FRAME:
                        received += 1
                        f = msg["body"]
                        self._log(
                            f"  CAM Frame #{f['frame_n']}  "
                            f"fps={f['fps']}  t={f['captured']:.1f}"
                        )
                        print(f"\n{'─'*62}")
                        print(f.get("data", "(no frame data)"))
                        print('─'*62)
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            self._log(f"✗ request_camera failed: {exc}")

    # ── Bonus: Discovery ──────────────────────────────────────────────────────

    def discover_nodes(self, timeout_s: float = 3.0) -> list[dict]:
        """Broadcast ANNOUNCE and collect ANNOUNCE_REPLY responses."""
        self._log("→ ANNOUNCE (UDP broadcast)")
        found = []
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(timeout_s)
                ann = build_msg(MsgKind.ANNOUNCE, STATION_ID, ALL_NODES, {
                    "station_id": STATION_ID,
                    "protocol":   PROTO_VERSION,
                    "purpose":    "TURBINE_CONTROL",
                })
                s.sendto(json.dumps(ann).encode(),
                         ("255.255.255.255", DISCOVER_PORT))
                deadline = time.time() + timeout_s
                while time.time() < deadline:
                    try:
                        data, addr = s.recvfrom(65535)
                        resp = json.loads(data.decode())
                        if resp.get("kind") == MsgKind.ANNOUNCE_REPLY:
                            found.append(resp["body"])
                            self._log(
                                f"← ANNOUNCE_REPLY from {resp['origin']}  "
                                f"caps={resp['body'].get('capabilities')}"
                            )
                    except socket.timeout:
                        break
        except OSError as exc:
            self._log(f"✗ discover_nodes failed: {exc}")
        self._log(f"  Discovered {len(found)} node(s)")
        return found

    # ── Bonus: Negotiation ────────────────────────────────────────────────────

    def negotiate(self, turbine_id: str) -> dict | None:
        """Send OFFER to request capabilities from a turbine."""
        self._log(f"→ OFFER to {turbine_id}")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3.0)
                offer = build_msg(MsgKind.OFFER, STATION_ID, turbine_id, {
                    "requested_services": [
                        "sensor_polling", "pitch_control",
                        "yaw_control", "telemetry_stream"
                    ],
                    "preferred_rate_hz":  2,
                    "data_format":        PROTO_VERSION,
                })
                s.sendto(json.dumps(offer).encode(),
                         (TURBINE_HOST, DISCOVER_PORT))
                data, _ = s.recvfrom(65535)
                resp = json.loads(data.decode())
                if resp.get("kind") == MsgKind.OFFER_REPLY:
                    body = resp["body"]
                    self._log(
                        f"← OFFER_REPLY  "
                        f"services={body.get('available_services')}  "
                        f"rate={body.get('telemetry_rate_hz')} Hz"
                    )
                    return body
        except (socket.timeout, OSError) as exc:
            self._log(f"✗ negotiate failed: {exc}")
        return None

    # ── Bonus: Agreement ──────────────────────────────────────────────────────

    def commit_session(self, turbine_id: str,
                       services: list[str]) -> bool:
        """Send COMMIT to formally start a joint operational session."""
        session_id = str(uuid.uuid4())[:8].upper()
        self._log(f"→ COMMIT to {turbine_id}  session={session_id}")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3.0)
                commit = build_msg(MsgKind.COMMIT, STATION_ID, turbine_id, {
                    "session_id":      session_id,
                    "agreed_services": services,
                    "start_time":      time.time(),
                    "duration_s":      3600,
                })
                s.sendto(json.dumps(commit).encode(),
                         (TURBINE_HOST, DISCOVER_PORT))
                data, _ = s.recvfrom(65535)
                resp = json.loads(data.decode())
                if resp.get("kind") == MsgKind.COMMIT_ACK:
                    status = resp["body"].get("status")
                    self._log(f"← COMMIT_ACK  status={status}  "
                              f"session={session_id}")
                    return status == "COMMITTED"
        except (socket.timeout, OSError) as exc:
            self._log(f"✗ commit_session failed: {exc}")
        return False

    # ── Bonus: Link status monitor ────────────────────────────────────────────

    def start_link_monitor(self):
        """Listen to gateway Ku-band link status broadcasts in background."""
        t = threading.Thread(target=self._link_status_listener,
                              name="LinkStatusMon", daemon=True)
        t.start()

    def _link_status_listener(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.bind(("", LINK_STATUS_PORT))
                s.settimeout(2.0)
                while self._running:
                    try:
                        data, _ = s.recvfrom(65535)
                        msg = json.loads(data.decode())
                        if msg.get("kind") == MsgKind.LINK_STATUS:
                            self._link_status = msg["body"]
                    except socket.timeout:
                        continue
        except OSError:
            pass

    # ── Security event handler ────────────────────────────────────────────────

    def _on_security_event(self, event: dict):
        self._log(
            f"🚨 SECURITY {event['code']} from {event['src']}: {event['detail']}"
        )

    # ── Status helpers ────────────────────────────────────────────────────────

    def print_link_status(self):
        ls = self._link_status
        if not ls:
            print("     (no link status received yet)")
            return
        vis = "✓ VISIBLE" if ls.get("satellite_visible") else "✗ BELOW HORIZON"
        print(
            f"  Ku-band link : {vis}  "
            f"El={ls.get('elevation_deg',0):.1f}°  "
            f"Delay={ls.get('propagation_delay_ms',0):.1f}ms  "
            f"FSPL={ls.get('path_loss_db',0):.1f}dB  "
            f"Rain={ls.get('rain_attenuation_db',0):.2f}dB  "
            f"Sea={ls.get('sea_description','?')}"
        )

    def telemetry_summary(self) -> dict:
        if not self._telem_frames:
            return {}
        pwr = [f["power_kw"] for f in self._telem_frames if "power_kw" in f]
        wnd = [f["wind_speed_ms"] for f in self._telem_frames
               if "wind_speed_ms" in f]
        return {
            "frames":        len(self._telem_frames),
            "avg_power_kw":  round(sum(pwr)/len(pwr), 1) if pwr else 0,
            "peak_power_kw": round(max(pwr), 1) if pwr else 0,
            "avg_wind_ms":   round(sum(wnd)/len(wnd), 2) if wnd else 0,
        }

    def stop(self):
        self._running = False