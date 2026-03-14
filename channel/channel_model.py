"""
channel_model.py
================
Irish Sea LEO Satellite Link Model — Arklow Bank Wind Park
==========================================================
Models the communication channel between Arklow Bank Wind Park
(52.47°N, 5.56°W, Irish Sea) and a LEO satellite at 600 km altitude.

Physical parameters (all from published ITU-R / ESA references):
  Orbit altitude    : 600 km
  Carrier frequency : 14.5 GHz  (Ku-band — used by real offshore SCADA)
  Min elevation     : 5°  (below this, horizon blockage)
  Orbital period    : ~96.7 min  (Kepler's 3rd law)
  Satellite velocity: ~7,558 m/s
  One-way delay     : 2.0 ms (zenith) – 9.6 ms (5° elevation)
  FSPL at zenith    : ~184.4 dB  (Ku-band, 600 km)
  Max Doppler       : ~365 kHz   (Ku-band)
  Irish Sea Hs      : 1.5 m      (Sea State 3, annual mean)
  Irish Sea Tp      : 6.5 s      (shorter period than open ocean)
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import math
import time
import random
import threading
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("LinkModel")

# ── Physical constants ────────────────────────────────────────────────────────
C               = 2.998e8          # speed of light, m/s
R_EARTH         = 6_371_000        # Earth mean radius, m
ORBIT_ALT       = 600_000          # satellite altitude, m  (600 km)
R_ORBIT         = R_EARTH + ORBIT_ALT
GM              = 3.986e14         # Earth gravitational parameter, m³/s²
ORBIT_PERIOD    = 2 * math.pi * math.sqrt(R_ORBIT**3 / GM)  # seconds (~5804 s)
V_SAT           = math.sqrt(GM / R_ORBIT)                   # orbital speed m/s

# Ku-band link parameters
KU_FREQ_HZ      = 14.5e9           # 14.5 GHz uplink centre frequency
KU_WAVELENGTH   = C / KU_FREQ_HZ

# Arklow Bank ground station coordinates
ARKLOW_LAT_DEG  = 52.47
ARKLOW_LON_DEG  = -5.56

# Minimum usable elevation angle
MIN_EL_DEG      = 5.0

# Irish Sea environmental parameters (Sea State 3)
IRISH_SEA_HS    = 1.5              # significant wave height, m
IRISH_SEA_TP    = 6.5              # peak wave period, s


# ── Geometry ──────────────────────────────────────────────────────────────────

def slant_range(el_deg: float) -> float:
    """
    Geometric slant range (m) from Arklow Bank to satellite
    at elevation angle el_deg.
    Uses the standard ground-station-to-satellite range formula.
    """
    el = math.radians(max(el_deg, 0.01))
    return R_EARTH * (
        math.sqrt((R_ORBIT / R_EARTH)**2 - math.cos(el)**2) - math.sin(el)
    )

def one_way_delay(el_deg: float) -> float:
    """One-way signal propagation delay in seconds."""
    return slant_range(el_deg) / C

def ku_fspl(el_deg: float) -> float:
    """
    Free-space path loss (dB) at Ku-band (14.5 GHz).
    FSPL = 20·log10(4π·d·f / c)
    """
    d = slant_range(el_deg)
    return 20.0 * math.log10(4.0 * math.pi * d * KU_FREQ_HZ / C)

def ku_doppler(el_deg: float) -> float:
    """
    Peak Doppler frequency shift (Hz) at given elevation.
    fd = (v_sat / c) × f × cos(el)
    At 14.5 GHz and 600 km orbit this peaks around ±365 kHz.
    """
    return (V_SAT / C) * KU_FREQ_HZ * math.cos(math.radians(el_deg))

def rain_attenuation_db(el_deg: float, rain_rate_mm_h: float = 5.0) -> float:
    """
    Simplified rain attenuation estimate (dB) using ITU-R P.838 specific
    attenuation coefficient for Ku-band at moderate Irish Sea rainfall.
    Default 5 mm/h is representative of light Irish rain.
    """
    # ITU-R P.838 coefficients for 14.5 GHz horizontal polarisation
    k, alpha = 0.0367, 1.147
    specific_atten = k * (rain_rate_mm_h ** alpha)   # dB/km
    path_len_km    = slant_range(el_deg) / math.sin(math.radians(max(el_deg, 5.0))) / 1000
    effective_len  = path_len_km * 0.01   # effective path fraction through rain layer
    return specific_atten * effective_len


# ── Satellite pass simulation ─────────────────────────────────────────────────

@dataclass
class OrbitalPass:
    """One satellite pass over Arklow Bank."""
    t_aos:   float    # Acquisition of Signal – simulated seconds
    t_los:   float    # Loss of Signal – simulated seconds
    peak_el: float    # peak elevation angle (degrees) during pass

    @property
    def pass_duration(self) -> float:
        return self.t_los - self.t_aos

    def elevation_at(self, t_sim: float) -> float:
        """
        Parabolic elevation profile during pass.
        Zero at AOS/LOS, peak_el at midpoint.
        """
        if not (self.t_aos <= t_sim <= self.t_los):
            return 0.0
        mid  = (self.t_aos + self.t_los) / 2.0
        half = self.pass_duration / 2.0
        return self.peak_el * max(0.0, 1.0 - ((t_sim - mid) / half) ** 2)


class IrishSeaOrbitSim:
    """
    Simulates LEO satellite passes as seen from Arklow Bank (52.47°N).
    At this latitude, passes occur roughly every 96 minutes with
    10-minute visibility windows.

    time_scale: simulated seconds per real second.
        60  → 1 real minute = 1 simulated hour  (recommended for demo)
        1   → real-time
    """

    def __init__(self, time_scale: float = 60.0, seed: int = 7):
        self.time_scale  = time_scale
        self._rng        = random.Random(seed)
        self._t0         = time.time()          # wall-clock start
        self._passes: list[OrbitalPass] = []
        self._build_schedule(n_passes=24)

    # ── Timing ────────────────────────────────────────────────────────────────

    def sim_now(self) -> float:
        """Current simulated time in seconds since start."""
        return (time.time() - self._t0) * self.time_scale

    # ── Pass schedule ──────────────────────────────────────────────────────────

    def _build_schedule(self, n_passes: int):
        """
        Generate n_passes orbital passes.
        Typical Arklow geometry: ~96 min between passes, ~10 min visible.
        """
        cursor = 0.0
        for _ in range(n_passes):
            gap      = self._rng.uniform(5200, 5900)    # ~87–98 min between passes
            duration = self._rng.uniform(500, 680)      # ~8–11 min visible
            peak_el  = self._rng.uniform(12.0, 78.0)    # elevation varies by pass
            t_aos    = cursor + gap
            self._passes.append(OrbitalPass(t_aos, t_aos + duration, peak_el))
            cursor   = t_aos + duration

    # ── Current state ──────────────────────────────────────────────────────────

    def active_pass(self) -> Optional[OrbitalPass]:
        t = self.sim_now()
        for p in self._passes:
            if p.t_aos <= t <= p.t_los:
                return p
        return None

    def current_elevation(self) -> float:
        p = self.active_pass()
        return p.elevation_at(self.sim_now()) if p else 0.0

    def link_available(self) -> bool:
        return self.current_elevation() >= MIN_EL_DEG

    def seconds_to_next_aos(self) -> Optional[float]:
        t = self.sim_now()
        for p in self._passes:
            if p.t_aos > t:
                return p.t_aos - t
        return None

    def snapshot(self) -> dict:
        el      = self.current_elevation()
        safe_el = max(el, 0.1)
        p       = self.active_pass()
        return {
            "sim_elapsed_s":        round(self.sim_now(), 1),
            "satellite_visible":    self.link_available(),
            "elevation_deg":        round(el, 2),
            "slant_range_km":       round(slant_range(safe_el) / 1000.0, 1),
            "propagation_delay_ms": round(one_way_delay(safe_el) * 1000.0, 2),
            "path_loss_db":         round(ku_fspl(safe_el), 1),
            "doppler_hz":           round(ku_doppler(max(el, MIN_EL_DEG)), 0),
            "rain_attenuation_db":  round(rain_attenuation_db(safe_el), 2),
            "pass_remaining_s":     round(p.t_los - self.sim_now(), 1) if p else None,
            "next_aos_s":           round(self.seconds_to_next_aos(), 1)
                                    if not self.link_available() else None,
        }


# ── Irish Sea impairment model ────────────────────────────────────────────────

# Irish Sea sea states (Douglas scale) with realistic Arklow Bank parameters
IRISH_SEA_STATES = {
    0: (0.0,  "Calm (glassy)",   0.000),
    1: (0.1,  "Calm (rippled)",  0.001),
    2: (0.5,  "Smooth",          0.003),
    3: (1.5,  "Slight",          0.009),   # Arklow annual average
    4: (2.5,  "Moderate",        0.016),
    5: (4.0,  "Rough",           0.025),
    6: (6.0,  "Very rough",      0.040),
}

@dataclass
class SeaConditions:
    scale:       int
    wave_hs_m:   float
    description: str
    base_per:    float       # packet error rate from antenna motion


class ArklowLinkModel:
    """
    Full Ku-band link impairment model for Arklow Bank → LEO satellite.

    Combines:
      1. Orbital geometry (delay, FSPL, Doppler)
      2. Irish Sea antenna motion packet loss
      3. Rain attenuation (Irish climate)
      4. Ionospheric scintillation bursts (rare at 52°N)
      5. Forced outage injection for testing
    """

    def __init__(self, time_scale: float = 60.0, sea_state: int = 3):
        self.orbit = IrishSeaOrbitSim(time_scale=time_scale)
        sc         = IRISH_SEA_STATES.get(sea_state, IRISH_SEA_STATES[3])
        self._sea  = SeaConditions(sc[0], sc[0], sc[1], sc[2])
        self._sea  = SeaConditions(
            scale       = sea_state,
            wave_hs_m   = sc[0],
            description = sc[1],
            base_per    = sc[2],
        )
        self._lock          = threading.Lock()
        self._forced_outage = False
        self._scint_active  = False
        self._scint_end     = 0.0

    # ── Link state ────────────────────────────────────────────────────────────

    def current_delay_s(self) -> float:
        el = max(self.orbit.current_elevation(), MIN_EL_DEG)
        return one_way_delay(el)

    def drop_packet(self) -> bool:
        """
        Simulate packet loss. Returns True if packet should be discarded.
        Accounts for: horizon blockage, forced outage, sea-state antenna
        motion, and ionospheric scintillation.
        """
        with self._lock:
            if self._forced_outage:
                return True
            if not self.orbit.link_available():
                return True

            # Ionospheric scintillation (less frequent at 52°N than equatorial)
            now = time.time()
            if not self._scint_active and random.random() < 0.000015:
                self._scint_active = True
                self._scint_end    = now + random.uniform(3.0, 12.0)
            if self._scint_active:
                if now < self._scint_end:
                    return random.random() < 0.35
                self._scint_active = False

            return random.random() < self._sea.base_per

    def simulate_delay(self):
        """Block calling thread for the current one-way propagation delay."""
        time.sleep(self.current_delay_s())

    def inject_outage(self, duration_s: float):
        """Force a link outage for `duration_s` real seconds."""
        logger.warning(f"[LinkModel] Outage injected for {duration_s:.1f}s")
        self._forced_outage = True
        def _restore():
            time.sleep(duration_s)
            self._forced_outage = False
            logger.info("[LinkModel] Outage cleared — link restored")
        threading.Thread(target=_restore, daemon=True).start()

    def inject_pass(self, duration_s: float = 600, peak_el: float = 45.0):
        """Force a satellite pass starting immediately (for demo use)."""
        t = self.orbit.sim_now()
        self.orbit._passes.insert(0, OrbitalPass(t, t + duration_s, peak_el))

    def status(self) -> dict:
        snap = self.orbit.snapshot()
        snap.update({
            "location":          f"Arklow Bank {ARKLOW_LAT_DEG}°N {abs(ARKLOW_LON_DEG):.2f}°W",
            "carrier_freq_ghz":  KU_FREQ_HZ / 1e9,
            "sea_state":         self._sea.scale,
            "sea_description":   self._sea.description,
            "wave_height_m":     self._sea.wave_hs_m,
            "packet_error_rate": round(self._sea.base_per, 5),
            "scintillation":     self._scint_active,
            "forced_outage":     self._forced_outage,
            "link_up":           self.orbit.link_available() and not self._forced_outage,
        })
        return snap


# ── Module-level singleton ────────────────────────────────────────────────────

_link_model: Optional[ArklowLinkModel] = None

def get_link(time_scale: float = 60.0) -> ArklowLinkModel:
    global _link_model
    if _link_model is None:
        _link_model = ArklowLinkModel(time_scale=time_scale, sea_state=3)
    return _link_model

# Alias for backward compatibility with demo.py imports
get_channel = get_link
ChannelModel = ArklowLinkModel
OrbitSimulator = IrishSeaOrbitSim
SatellitePass = OrbitalPass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    model = get_link(time_scale=300)
    print("=== Arklow Bank Link Model Self-Test ===\n")
    print(f"Orbit altitude  : {ORBIT_ALT/1000:.0f} km")
    print(f"Carrier         : {KU_FREQ_HZ/1e9:.1f} GHz (Ku-band)")
    print(f"Orbital period  : {ORBIT_PERIOD/60:.1f} min")
    print(f"Satellite speed : {V_SAT:.0f} m/s")
    print()
    for i in range(10):
        time.sleep(0.4)
        s = model.status()
        tag = "UP  " if s["link_up"] else "DOWN"
        print(
            f"[{tag}] El={s['elevation_deg']:>6.1f}°  "
            f"Delay={s['propagation_delay_ms']:>6.2f}ms  "
            f"FSPL={s['path_loss_db']:>6.1f}dB  "
            f"Rain={s['rain_attenuation_db']:.2f}dB  "
            f"Doppler={s['doppler_hz']:.0f}Hz"
        )