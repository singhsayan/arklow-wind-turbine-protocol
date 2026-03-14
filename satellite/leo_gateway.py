"""
leo_gateway.py
==============
Ku-Band LEO Satellite Gateway — Arklow Bank Wind Park
======================================================
Relays messages between the Arklow Bank space station control centre
and the GE 3.6-100 turbine, applying the Irish Sea Ku-band channel model.

Port 9010  TCP  – Uplink receiver (space station connects here)
Port 9012  UDP  – Link status broadcast (every 2 seconds)

Relay pipeline per message:
  1. Receive from space station
  2. Check link availability (orbital geometry)
  3. Apply packet loss model (sea state + scintillation)
  4. Apply one-way propagation delay  (time.sleep)
  5. Forward to turbine on appropriate port
  6. Apply return path delay + independent loss check
  7. Return turbine response to space station

If the link is down or a packet is lost, a REJECT is returned immediately
so the space station knows to retry when the satellite is next overhead.
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys
import os

import socket
import threading
import time
import json
import logging
import signal

from utils.protocol import (
    build_msg, tcp_send, tcp_recv, make_reject,
    fmt_msg, MsgKind, ALL_NODES
)
from channel.channel_model import get_link, ArklowLinkModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("KuBandGateway")

GATEWAY_ID      = "ARKLOW_SAT_GW_1"
BIND_ADDR       = "0.0.0.0"
PORT_UPLINK     = 9010
PORT_LINK_STATUS= 9012
STATUS_INTERVAL = 2.0   # seconds between status broadcasts

TURBINE_HOST    = "127.0.0.1"
# Map message kinds to turbine ports
KIND_PORT_MAP = {
    MsgKind.SENSOR_POLL:  9001,
    MsgKind.PING:         9001,
    MsgKind.CMD_PITCH:    9002,
    MsgKind.CMD_ESTOP:    9002,
    MsgKind.TELEM_START:  9004,
    MsgKind.TELEM_STOP:   9004,
}
DEFAULT_TURBINE_PORT = 9001


class KuBandGateway:
    """
    Ku-band satellite gateway applying realistic Irish Sea channel physics
    to all traffic between the space station and Arklow Bank turbines.
    """

    def __init__(self, time_scale: float = 60.0):
        self._link    = get_link(time_scale)
        self._running = True
        self._counters = {
            "relayed":       0,
            "dropped_up":    0,
            "dropped_down":  0,
            "no_link":       0,
        }

    def start(self):
        logger.info(f"╔══ {GATEWAY_ID} ══╗")
        logger.info(f"  Orbit     : 600 km LEO")
        logger.info(f"  Carrier   : 14.5 GHz Ku-band")
        logger.info(f"  Ground    : Arklow Bank 52.47°N 5.56°W")
        logger.info(f"  Uplink    : TCP {BIND_ADDR}:{PORT_UPLINK}")
        logger.info(f"  Timescale : {self._link.orbit.time_scale}×")

        for name, fn in [
            ("UplinkSrv",    self._uplink_server),
            ("StatusBcast",  self._status_broadcaster),
            ("LinkMonitor",  self._link_monitor),
        ]:
            threading.Thread(target=fn, name=name, daemon=True).start()

        logger.info("[KuBandGateway] Running.")

    def stop(self):
        self._running = False

    # ── Uplink server ─────────────────────────────────────────────────────────

    def _uplink_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((BIND_ADDR, PORT_UPLINK))
            srv.listen(8)
            srv.settimeout(1.0)
            logger.info(f"[UplinkSrv] Listening on TCP {PORT_UPLINK}")
            while self._running:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._relay_session,
                    args=(conn, addr), daemon=True
                ).start()

    def _relay_session(self, conn, addr):
        peer = f"{addr[0]}:{addr[1]}"
        logger.info(f"[Relay] Control station connected: {peer}")
        with conn:
            conn.settimeout(60.0)
            while self._running:
                msg = tcp_recv(conn)
                if msg is None:
                    break

                delay  = self._link.current_delay_s()
                lost   = self._link.drop_packet()
                status = self._link.status()

                logger.info(
                    f"[Relay] {fmt_msg(msg)} | "
                    f"delay={delay*1000:.1f}ms | "
                    f"link={'UP' if status['link_up'] else 'DOWN'} | "
                    f"{'DROP' if lost else 'PASS'}"
                )

                # Link down — reject immediately
                if not status["link_up"]:
                    self._counters["no_link"] += 1
                    reject = make_reject(msg, GATEWAY_ID,
                                         reason="SATELLITE_NOT_VISIBLE")
                    reject["body"]["elevation_deg"] = status["elevation_deg"]
                    reject["body"]["next_aos_s"]    = status.get("next_aos_s")
                    tcp_send(conn, reject)
                    continue

                # Packet lost on uplink
                if lost:
                    self._counters["dropped_up"] += 1
                    reject = make_reject(msg, GATEWAY_ID,
                                         reason="KU_BAND_PACKET_LOSS")
                    reject["body"]["sea_state"] = status["sea_description"]
                    reject["body"]["per"]       = status["packet_error_rate"]
                    tcp_send(conn, reject)
                    continue

                # Apply uplink propagation delay
                time.sleep(delay)

                # Forward to turbine
                turbine_resp = self._forward_to_turbine(msg)

                if turbine_resp is None:
                    self._counters["dropped_up"] += 1
                    tcp_send(conn, make_reject(
                        msg, GATEWAY_ID, reason="TURBINE_UNREACHABLE"))
                    continue

                # Apply return path delay + independent loss check
                time.sleep(delay)
                if self._link.drop_packet():
                    self._counters["dropped_down"] += 1
                    logger.warning("[Relay] Return path packet lost")
                    # Don't send anything — station will timeout and retry
                    continue

                tcp_send(conn, turbine_resp)
                self._counters["relayed"] += 1

        logger.info(f"[Relay] Control station disconnected: {peer}")

    def _forward_to_turbine(self, msg: dict):
        """
        Open a short-lived TCP connection to the appropriate turbine port
        and exchange one request-response pair.
        """
        port = KIND_PORT_MAP.get(msg.get("kind", ""), DEFAULT_TURBINE_PORT)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5.0)
                s.connect((TURBINE_HOST, port))
                tcp_send(s, msg)
                return tcp_recv(s)
        except (ConnectionRefusedError, socket.timeout, OSError) as exc:
            logger.warning(f"[Relay] Turbine port {port} unreachable: {exc}")
            return None

    # ── Link status broadcaster ───────────────────────────────────────────────

    def _status_broadcaster(self):
        """Broadcast Ku-band link quality report via UDP every STATUS_INTERVAL s."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while self._running:
                time.sleep(STATUS_INTERVAL)
                report = self._link.status()
                report["gateway_id"] = GATEWAY_ID
                report["counters"]   = dict(self._counters)
                broadcast = build_msg(
                    MsgKind.LINK_STATUS, GATEWAY_ID, ALL_NODES, report
                )
                try:
                    sock.sendto(json.dumps(broadcast).encode(),
                                ("255.255.255.255", PORT_LINK_STATUS))
                except OSError:
                    pass

    # ── Link state monitor ────────────────────────────────────────────────────

    def _link_monitor(self):
        """Log transitions between satellite visible / below horizon."""
        previous = None
        while self._running:
            time.sleep(1.0)
            current = self._link.orbit.link_available()
            if current != previous:
                s = self._link.status()
                if current:
                    logger.info(
                        f"[LinkMonitor] ★ Ku-band LINK UP — "
                        f"El={s['elevation_deg']:.1f}°  "
                        f"Delay={s['propagation_delay_ms']:.1f}ms  "
                        f"FSPL={s['path_loss_db']:.1f}dB  "
                        f"Rain={s['rain_attenuation_db']:.2f}dB"
                    )
                else:
                    nxt = s.get("next_aos_s")
                    nxt_str = f"{nxt:.0f}s" if nxt else "unknown"
                    logger.warning(
                        f"[LinkMonitor] ✗ Ku-band LINK DOWN — "
                        f"next AOS in {nxt_str} (simulated)"
                    )
                previous = current


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Arklow Bank Ku-band LEO Gateway")
    ap.add_argument("--timescale", type=float, default=60.0)
    args = ap.parse_args()

    gw = KuBandGateway(time_scale=args.timescale)
    gw.start()

    def _shutdown(sig, frame):
        print("\nShutting down gateway…")
        gw.stop()

    signal.signal(signal.SIGINT, _shutdown)
    while gw._running:
        time.sleep(1)