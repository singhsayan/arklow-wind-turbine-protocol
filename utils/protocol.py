"""
protocol.py
===========
Arklow Bank Offshore Wind Control Protocol  –  ABWCP/1.0
=========================================================
Custom application-layer protocol built on raw TCP/UDP sockets for
communication between the Arklow Bank Wind Park (Irish Sea) and a
LEO satellite-based space station control centre.

Wire format per message:
    [4 bytes big-endian payload length][JSON-UTF8 body]

Message schema:
{
    "proto":     "ABWCP/1.0",
    "mid":       "<uuid4>",
    "sent_at":   <unix epoch float>,
    "seq":       <int>,
    "kind":      "<MSG_KIND>",
    "origin":    "<node_id>",
    "target":    "<node_id | ALL>",
    "body":      { ... },
    "integrity": "<sha256 hex 10 chars>"
}

Integrity field:
    SHA-256( proto | mid | kind | origin | target | canonical-JSON(body) )
    We take the first 10 hex characters (40-bit) — sufficient for
    tamper detection on a low-bandwidth satellite link.

Reliability:
    - CONFIRM / REJECT messages (ACK/NACK equivalent)
    - Per-connection sequence number tracking
    - Retransmit logic handled by the caller (ARQ)
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import json
import time
import uuid
import hashlib
import logging
from typing import Optional

logger = logging.getLogger("ABWCP")

# Protocol identity
PROTO_VERSION  = "ABWCP/1.0"
ALL_NODES      = "ALL"
MAX_FRAME_BYTES = 65_536   # max wire frame size (64 KiB)

# ── Message kinds ─────────────────────────────────────────────────────────────

class MsgKind:
    # Actuator commands
    CMD_PITCH      = "CMD_PITCH"       # set blade collective pitch
    CMD_YAW        = "CMD_YAW"         # rotate nacelle to bearing
    CMD_ESTOP      = "CMD_ESTOP"       # emergency feather + halt

    # Sensor / telemetry
    SENSOR_POLL    = "SENSOR_POLL"     # request one sensor snapshot
    SENSOR_REPLY   = "SENSOR_REPLY"    # sensor snapshot response
    TELEM_FRAME    = "TELEM_FRAME"     # continuous telemetry push
    TELEM_START    = "TELEM_START"     # begin telemetry subscription
    TELEM_STOP     = "TELEM_STOP"      # cancel telemetry subscription

    # O&M video (bonus)
    CAM_REQUEST    = "CAM_REQUEST"     # request live camera feed
    CAM_FRAME      = "CAM_FRAME"       # one encoded camera frame

    # Reliability / keepalive
    CONFIRM        = "CONFIRM"         # positive acknowledgement
    REJECT         = "REJECT"          # negative ack – request retransmit
    PING           = "PING"            # liveness probe
    PONG           = "PONG"            # liveness response

    # Node discovery & coordination (bonus)
    ANNOUNCE       = "ANNOUNCE"        # node announces its presence
    ANNOUNCE_REPLY = "ANNOUNCE_REPLY"  # response to announcement
    OFFER          = "OFFER"           # offer capabilities for negotiation
    OFFER_REPLY    = "OFFER_REPLY"     # capability negotiation response
    COMMIT         = "COMMIT"          # agree to start a joint session
    COMMIT_ACK     = "COMMIT_ACK"      # session commitment accepted
    COMMIT_DECLINE = "COMMIT_DECLINE"  # session commitment declined

    # Security (bonus)
    SEC_ALERT      = "SEC_ALERT"       # security anomaly detected
    SEC_BAN        = "SEC_BAN"         # ban notification for a node

    # Infrastructure
    LINK_STATUS    = "LINK_STATUS"     # satellite link quality report

# ── Integrity hash ────────────────────────────────────────────────────────────

def _integrity(proto: str, mid: str, kind: str,
               origin: str, target: str, body: dict) -> str:
    """
    Compute integrity stamp: first 10 hex chars of SHA-256 over all
    header fields plus canonical JSON of the body.
    Canonical = sorted keys, no extra whitespace.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    raw = f"{proto}\x00{mid}\x00{kind}\x00{origin}\x00{target}\x00{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]

# ── Sequence counter (module-level, thread-safe enough for demo) ──────────────

_seq = 0

def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq

# ── Message construction ──────────────────────────────────────────────────────

def build_msg(kind: str, origin: str, target: str,
              body: dict, seq: Optional[int] = None) -> dict:
    """Construct a fully populated ABWCP/1.0 message dict."""
    mid = str(uuid.uuid4())
    s   = seq if seq is not None else _next_seq()
    return {
        "proto":     PROTO_VERSION,
        "mid":       mid,
        "sent_at":   time.time(),
        "seq":       s,
        "kind":      kind,
        "origin":    origin,
        "target":    target,
        "body":      body,
        "integrity": _integrity(PROTO_VERSION, mid, kind, origin, target, body),
    }

# ── Wire encoding / decoding ──────────────────────────────────────────────────

def frame_encode(msg: dict) -> bytes:
    """Encode msg to: [4-byte big-endian length][UTF-8 JSON]."""
    raw    = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    header = len(raw).to_bytes(4, byteorder="big")
    return header + raw

def frame_decode(data: bytes) -> dict:
    """Decode raw bytes (no length prefix) back to message dict."""
    return json.loads(data.decode("utf-8"))

# ── TCP socket helpers ────────────────────────────────────────────────────────

def tcp_send(sock, msg: dict) -> bool:
    """Send one ABWCP frame over a connected TCP socket."""
    try:
        sock.sendall(frame_encode(msg))
        return True
    except OSError as exc:
        logger.warning(f"tcp_send failed: {exc}")
        return False

def tcp_recv(sock) -> Optional[dict]:
    """
    Receive one ABWCP frame from a TCP socket.
    Returns None on connection close, integrity failure, or error.
    """
    try:
        hdr = _read_exact(sock, 4)
        if hdr is None:
            return None
        length = int.from_bytes(hdr, byteorder="big")
        if length == 0 or length > MAX_FRAME_BYTES:
            logger.error(f"tcp_recv: bad frame length {length}")
            return None
        payload = _read_exact(sock, length)
        if payload is None:
            return None
        msg = json.loads(payload.decode("utf-8"))
        if not check_integrity(msg):
            logger.warning(
                f"tcp_recv: integrity failure on mid={msg.get('mid','?')}"
            )
            return None
        return msg
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug(f"tcp_recv error: {exc}")
        return None

def _read_exact(sock, n: int) -> Optional[bytes]:
    """Read exactly n bytes from socket, returning None on EOF or error."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

# ── Integrity verification ────────────────────────────────────────────────────

def check_integrity(msg: dict) -> bool:
    """Return True if the integrity stamp on msg matches recomputed value."""
    try:
        expected = _integrity(
            msg["proto"], msg["mid"], msg["kind"],
            msg["origin"], msg["target"], msg["body"]
        )
        return expected == msg.get("integrity")
    except (KeyError, TypeError):
        return False

# ── Convenience builders ──────────────────────────────────────────────────────

def make_confirm(original: dict, origin: str, extra: dict = None) -> dict:
    """Build a CONFIRM in reply to `original`."""
    body = {"ref_mid": original["mid"], "ref_seq": original["seq"], "ok": True}
    if extra:
        body.update(extra)
    return build_msg(MsgKind.CONFIRM, origin=origin,
                     target=original["origin"], body=body)

def make_reject(original: dict, origin: str, reason: str = "") -> dict:
    """Build a REJECT in reply to `original`."""
    return build_msg(MsgKind.REJECT, origin=origin,
                     target=original["origin"],
                     body={"ref_mid": original["mid"],
                           "ref_seq": original["seq"],
                           "ok": False, "reason": reason})

# ── Diagnostic pretty-print ───────────────────────────────────────────────────

def fmt_msg(msg: dict) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(msg.get("sent_at", 0)))
    return (
        f"[{ts}] {msg.get('kind','?'):18s} "
        f"{msg.get('origin','?'):22s}→ {msg.get('target','?'):22s}"
        f" seq={msg.get('seq','?'):>5}"
    )


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    m = build_msg(MsgKind.CMD_PITCH, "ARKLOW_CTRL_1",
                  "ARKLOW_GE3600_T1", {"pitch_deg": 12.5})
    wire = frame_encode(m)
    print(f"Frame size : {len(wire)} bytes")
    print(fmt_msg(m))

    # integrity check
    assert check_integrity(m), "Integrity check failed!"
    print("Integrity OK ✓")

    # tamper test
    m["body"]["pitch_deg"] = 95.0
    assert not check_integrity(m), "Tamper should have been detected!"
    print("Tamper detection OK ✓")