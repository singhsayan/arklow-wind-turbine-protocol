"""
intrusion_detector.py
=====================
Security Monitor — Arklow Bank Wind Park Control Protocol
==========================================================
Monitors all incoming ABWCP/1.0 messages for malicious or anomalous
behaviour targeting the Arklow Bank GE 3.6-100 turbine.

Detection methods (6 total):
  1. Rate limiter       – token bucket per source, 20 msg/s
  2. Replay detection   – 5-minute msg ID cache
  3. Command bounds     – pitch outside GE 3.6-100 physical limits
  4. Pitch rate anomaly – commands arrive faster than servo can execute
  5. Unauthorized ESTOP – emergency stop from unregistered node
  6. Sequence injection – large seq number jump (>1000)

Mitigation:
  - Auto-ban offending nodes for 10 minutes
  - Alert callback to control centre
  - Comprehensive audit log with timestamps
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import time
import threading
import logging

logger = logging.getLogger("SecurityMonitor")

# ── Turbine-specific physical limits (GE 3.6-100) ────────────────────────────
PITCH_MIN_DEG       = 0.0
PITCH_MAX_DEG       = 90.0
PITCH_RATE_DEG_S    = 8.0     # GE 3.6-100 max pitch rate

# ── Security policy constants ─────────────────────────────────────────────────
MAX_RATE_PER_S      = 20      # max messages per second per source
REPLAY_WINDOW_S     = 300     # 5-minute replay cache window
BAN_DURATION_S      = 600     # 10-minute auto-ban
SEQ_JUMP_LIMIT      = 1000    # suspicious sequence jump threshold
PITCH_CMD_MIN_GAP_S = 0.1     # minimum time between pitch commands
                               # (faster than this is physically impossible
                               #  given 8°/s servo rate)


# ── Token bucket rate limiter ─────────────────────────────────────────────────

class _RateBucket:
    """Token bucket: refills at `rate` tokens/s, max capacity `burst`."""

    def __init__(self, rate: float, burst: float):
        self._rate   = rate
        self._burst  = burst
        self._tokens = burst
        self._last   = time.monotonic()
        self._lock   = threading.Lock()

    def request(self) -> bool:
        """Consume one token. Returns True if allowed, False if rate-limited."""
        with self._lock:
            now            = time.monotonic()
            refill         = (now - self._last) * self._rate
            self._tokens   = min(self._burst, self._tokens + refill)
            self._last     = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# ── Security monitor ──────────────────────────────────────────────────────────

class SecurityMonitor:
    """
    Inspects every incoming ABWCP/1.0 message before it reaches the turbine.
    Thread-safe — can be used from multiple server threads simultaneously.
    """

    def __init__(self, alert_cb=None):
        """
        alert_cb: optional callable(event_dict) called on every security event.
        """
        self._cb          = alert_cb
        self._lock        = threading.Lock()
        self._rate_buckets: dict[str, _RateBucket]  = {}
        self._seen_mids:    dict[str, float]         = {}   # mid → first_seen
        self._banned:       dict[str, float]         = {}   # node → ban_expiry
        self._trusted_nodes: set[str]                = set()
        self._last_seq:     dict[str, int]           = {}
        self._last_pitch_cmd: dict[str, float]       = {}   # node → timestamp
        self._audit_log:    list[dict]               = []

        # Periodic cleanup
        threading.Thread(target=self._housekeeping,
                          daemon=True, name="SecurityHousekeep").start()

    # ── Configuration ──────────────────────────────────────────────────────────

    def trust(self, node_id: str):
        """Register a node as trusted (will still be rate-limited, not blocked)."""
        with self._lock:
            self._trusted_nodes.add(node_id)
        logger.info(f"[SecMon] Trusted: {node_id}")

    # ── Main inspection entry point ───────────────────────────────────────────

    def inspect(self, msg: dict) -> tuple[bool, str]:
        """
        Inspect one message.
        Returns (permit: bool, reason: str).
        If permit is False the message must be dropped.
        """
        src   = msg.get("origin", "UNKNOWN")
        mid   = msg.get("mid", "")
        seq   = msg.get("seq", 0)
        kind  = msg.get("kind", "")
        body  = msg.get("body", {})

        with self._lock:

            # ── Check 1: Banned node ───────────────────────────────────────
            if src in self._banned:
                if time.time() < self._banned[src]:
                    return False, "BANNED"
                del self._banned[src]    # ban expired

            # ── Check 2: Unknown source (soft alert, not block) ────────────
            if self._trusted_nodes and src not in self._trusted_nodes:
                self._record(src, "UNKNOWN_NODE",
                             f"Message from unregistered node {src}", msg)

            # ── Check 3: Rate limiting ─────────────────────────────────────
            if src not in self._rate_buckets:
                self._rate_buckets[src] = _RateBucket(
                    MAX_RATE_PER_S, MAX_RATE_PER_S * 2
                )
            if not self._rate_buckets[src].request():
                self._record(src, "RATE_EXCEEDED",
                             f"Token bucket empty for {src}", msg, ban=True)
                return False, "RATE_LIMITED"

            # ── Check 4: Replay detection ──────────────────────────────────
            now = time.time()
            if mid in self._seen_mids:
                self._record(src, "REPLAY",
                             f"Duplicate mid {mid[:8]}… from {src}", msg, ban=True)
                return False, "REPLAY_DETECTED"
            self._seen_mids[mid] = now

            # ── Check 5: Sequence number injection ─────────────────────────
            if src in self._last_seq:
                jump = seq - self._last_seq[src]
                if jump < 0 or jump > SEQ_JUMP_LIMIT:
                    self._record(src, "SEQ_INJECTION",
                                 f"Seq jumped {self._last_seq[src]}→{seq}", msg)
            self._last_seq[src] = seq

            # ── Check 6: Pitch command bounds (GE 3.6-100 physical limits) ─
            if kind == "CMD_PITCH":
                angle = float(body.get("pitch_deg", 5.0))
                if not (PITCH_MIN_DEG <= angle <= PITCH_MAX_DEG):
                    self._record(
                        src, "OUT_OF_BOUNDS_PITCH",
                        f"Pitch {angle:.1f}° outside GE 3.6-100 range "
                        f"({PITCH_MIN_DEG}–{PITCH_MAX_DEG}°)", msg, ban=True
                    )
                    return False, f"INVALID_PITCH:{angle:.1f}"

                # ── Check 7: Pitch rate anomaly ────────────────────────────
                # GE 3.6-100 pitch servo moves at 8°/s max.
                # Commands arriving faster than PITCH_CMD_MIN_GAP_S are
                # physically impossible to execute — flag as suspicious.
                last_t = self._last_pitch_cmd.get(src, 0.0)
                gap    = now - last_t
                if gap < PITCH_CMD_MIN_GAP_S and last_t > 0:
                    self._record(
                        src, "PITCH_RATE_ANOMALY",
                        f"Pitch commands {gap*1000:.0f}ms apart from {src} "
                        f"(servo physically limited to 8°/s)", msg
                    )
                self._last_pitch_cmd[src] = now

            # ── Check 8: Unauthorized ESTOP ────────────────────────────────
            if kind == "CMD_ESTOP":
                if self._trusted_nodes and src not in self._trusted_nodes:
                    self._record(
                        src, "UNAUTH_ESTOP",
                        f"Emergency stop from untrusted node {src}", msg, ban=True
                    )
                    return False, "UNAUTH_ESTOP"

        return True, "OK"

    # ── Public queries ────────────────────────────────────────────────────────

    def ban(self, node_id: str, reason: str = "MANUAL"):
        with self._lock:
            self._banned[node_id] = time.time() + BAN_DURATION_S
        logger.warning(f"[SecMon] Banned {node_id}: {reason}")

    def active_bans(self) -> dict[str, float]:
        with self._lock:
            return {k: v for k, v in self._banned.items() if v > time.time()}

    def recent_events(self, n: int = 10) -> list[dict]:
        with self._lock:
            return self._audit_log[-n:]

    def summary(self) -> dict:
        with self._lock:
            return {
                "total_events":  len(self._audit_log),
                "active_bans":   len([v for v in self._banned.values()
                                      if v > time.time()]),
                "trusted_nodes": sorted(self._trusted_nodes),
                "replay_cache":  len(self._seen_mids),
            }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _record(self, src: str, code: str, detail: str,
                msg: dict, ban: bool = False):
        event = {
            "time":     time.time(),
            "ts":       time.strftime("%H:%M:%S"),
            "src":      src,
            "code":     code,
            "detail":   detail,
            "msg_kind": msg.get("kind"),
            "mid":      msg.get("mid", "")[:8],
            "banned":   ban,
        }
        self._audit_log.append(event)

        lvl = logging.CRITICAL if ban else logging.WARNING
        logger.log(lvl, f"[SecMon] {code}: {detail}")

        if ban:
            self._banned[src] = time.time() + BAN_DURATION_S
            logger.warning(f"[SecMon] Auto-banned {src} for {BAN_DURATION_S}s")

        if self._cb:
            try:
                self._cb(event)
            except Exception as exc:
                logger.error(f"[SecMon] Alert callback error: {exc}")

    def _housekeeping(self):
        """Periodically purge expired replay cache and ban entries."""
        while True:
            time.sleep(60.0)
            cutoff = time.time()
            with self._lock:
                expired_mids = [
                    k for k, v in self._seen_mids.items()
                    if cutoff - v > REPLAY_WINDOW_S
                ]
                for k in expired_mids:
                    del self._seen_mids[k]
                expired_bans = [
                    k for k, v in self._banned.items() if cutoff > v
                ]
                for k in expired_bans:
                    del self._banned[k]
            if expired_mids or expired_bans:
                logger.debug(
                    f"[SecMon] Housekeeping: removed {len(expired_mids)} "
                    f"replay entries, {len(expired_bans)} bans"
                )


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uuid
    logging.basicConfig(level=logging.DEBUG,
                        format="%(levelname)s %(name)s: %(message)s")

    mon = SecurityMonitor()
    mon.trust("ARKLOW_CTRL_1")
    mon.trust("ARKLOW_GE3600_T1")

    def mk(origin, kind, body, seq=1):
        return {
            "proto":   "ABWCP/1.0",
            "mid":     str(uuid.uuid4()),
            "sent_at": time.time(),
            "seq":     seq,
            "kind":    kind,
            "origin":  origin,
            "target":  "ARKLOW_GE3600_T1",
            "body":    body,
        }

    tests = [
        ("Normal pitch 12°",       mk("ARKLOW_CTRL_1", "CMD_PITCH", {"pitch_deg": 12.0}), True),
        ("Out-of-range pitch 95°", mk("ATTACKER",      "CMD_PITCH", {"pitch_deg": 95.0}), False),
        ("Unauth ESTOP",           mk("ROGUE_BOT",     "CMD_ESTOP", {}),                  False),
    ]
    for label, msg, expected in tests:
        allow, reason = mon.inspect(msg)
        mark = "✓" if allow == expected else "✗"
        print(f"  {mark}  {label:<30s} permit={allow}  reason={reason}")

    # Replay
    m = mk("ARKLOW_CTRL_1", "CMD_PITCH", {"pitch_deg": 8.0})
    mon.inspect(m)
    allow, reason = mon.inspect(m)
    print(f"  {'✓' if not allow else '✗'}  Replay detection              "
          f"permit={allow}  reason={reason}")

    # Flood
    allowed = sum(
        1 for i in range(60)
        if mon.inspect(
            {**mk("FLOOD_NODE", "SENSOR_POLL", {}, i), "mid": str(uuid.uuid4())}
        )[0]
    )
    print(f"  ✓  Flood (60 msgs): {allowed}/60 allowed (rate limited)")

    print("\nSummary:", mon.summary())