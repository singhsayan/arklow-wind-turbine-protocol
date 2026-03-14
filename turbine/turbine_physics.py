"""
turbine_physics.py
==================
GE 3.6-100 Wind Turbine Physics — Arklow Bank Wind Park
========================================================
Simulates one GE 3.6 MW turbine as deployed at Arklow Bank Wind Park,
operational since 2004 — the first commercial offshore wind farm in Ireland.

Farm:       Arklow Bank Wind Park, Co. Wicklow, Ireland
Turbine:    GE 3.6-100 (3.6 MW, 100 m rotor, hub height 75 m)
Capacity:   7 turbines × 3.6 MW = 25.2 MW total installed
Location:   52.47°N, 5.56°W — Irish Sea, ~10 km offshore
Water depth: 3–8 m (monopile foundations)

Wind climate:
  Mean wind speed:        9.8 m/s (published Arklow Bank site data)
  Prevailing direction:   SW (225°) — typical Irish Sea
  Turbulence intensity:   0.07 (offshore, lower than onshore)

Irish Sea sea conditions:
  Significant wave height: 1.5 m  (annual mean, Sea State 3)
  Peak wave period:        6.5 s  (shorter than open Atlantic)
  Platform motion:         monopile — heave/surge much smaller than floating
"""

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import math
import time
import random
import threading
import logging
import copy
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("TurbinePhysics")

# ── GE 3.6-100 turbine constants ──────────────────────────────────────────────
# Source: GE Energy 3.6 MW Offshore Wind Turbine specification sheet
RATED_POWER_KW      = 3_600.0   # rated electrical output
RATED_WIND_MS       = 14.0      # wind speed at rated power
CUT_IN_MS           = 3.5       # minimum operating wind speed
CUT_OUT_MS          = 27.0      # maximum operating wind speed (storm cut-out)
ROTOR_D_M           = 100.0     # rotor diameter
ROTOR_RADIUS_M      = ROTOR_D_M / 2.0
SWEPT_AREA_M2       = math.pi * ROTOR_RADIUS_M**2    # 7,854 m²
AIR_DENSITY         = 1.225     # kg/m³ at sea level (Irish Sea)
MAX_CP              = 0.46      # peak power coefficient — GE 3.6-100
LAMBDA_OPT          = 7.5       # optimal tip-speed ratio for GE 3.6-100
PITCH_RATE_DEG_S    = 8.0       # max collective pitch rate °/s
YAW_RATE_DEG_S      = 0.5       # max nacelle yaw rate °/s
PITCH_MIN_DEG       = 0.0       # fine pitch (maximum power)
PITCH_MAX_DEG       = 90.0      # feathered (zero aerodynamic torque)
RATED_RPM           = 20.0      # rated rotor speed (RPM) — GE 3.6-100
MAX_RPM             = 22.0      # overspeed limit (RPM)
HUB_HEIGHT_M        = 75.0      # hub height above mean sea level

# Generator efficiency and power factor
GEN_EFFICIENCY      = 0.935
POWER_FACTOR        = 0.95

# Arklow Bank wind climatology
ARKLOW_WIND_MEAN_MS = 9.8       # annual mean wind speed (published)
ARKLOW_WIND_DIR_DEG = 225.0     # prevailing SW wind direction
ARKLOW_TI           = 0.07      # offshore turbulence intensity

# Irish Sea monopile platform motion (much smaller than floating turbine)
IRISH_SEA_HS        = 1.5       # significant wave height (m)
IRISH_SEA_TP        = 6.5       # peak wave period (s)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MonopileMotion:
    """
    Simplified 3-DOF motion for a monopile-founded turbine.
    Amplitudes are small compared to a floating platform.
    """
    fore_aft_mm:  float = 0.0    # tower top fore-aft displacement (mm)
    side_side_mm: float = 0.0    # tower top side-side displacement (mm)
    tilt_mdeg:    float = 0.0    # tilt angle (milli-degrees)


@dataclass
class GE3600State:
    """Complete operational state of one GE 3.6-100 turbine."""

    # Blade pitch (collective)
    pitch_demand_deg:   float = 4.0    # operator setpoint
    pitch_actual_deg:   float = 4.0    # actual blade angle (servo lag)

    # Nacelle yaw
    yaw_demand_deg:     float = 225.0  # operator setpoint (SW = prevailing)
    yaw_actual_deg:     float = 225.0  # actual nacelle bearing

    # Drivetrain
    rotor_rpm:          float = 0.0
    gen_rpm:            float = 0.0    # gearbox ratio ~97:1 for GE 3.6
    power_kw:           float = 0.0
    reactive_power_kvar:float = 0.0

    # Wind at hub height
    wind_speed_ms:      float = ARKLOW_WIND_MEAN_MS
    wind_dir_deg:       float = ARKLOW_WIND_DIR_DEG
    wind_turbulence_ms: float = 0.0    # turbulent component

    # Temperatures (°C)
    temp_nacelle_c:     float = 22.0
    temp_gearbox_c:     float = 48.0
    temp_main_bearing_c:float = 35.0   # additional sensor vs original
    temp_generator_c:   float = 62.0
    temp_blade_root_c:  float = 14.0
    temp_ambient_c:     float = 12.0   # Irish Sea mean ~12°C

    # Structural
    tower_motion: MonopileMotion = field(default_factory=MonopileMotion)
    blade_vibration_hz: float = 0.0    # dominant blade vibration frequency

    # Condition monitoring
    gearbox_vibration_g: float = 0.1   # gearbox vibration level (g-rms)
    main_bearing_grease_temp_c: float = 38.0

    # Fault / control state
    fault_code:         int   = 0
    fault_description:  str   = ""
    e_stop_active:      bool  = False
    curtailment_pct:    float = 100.0  # 100 = full output, lower = curtailed

    # Counters
    op_hours:           float = 18_650.0  # Arklow has been running since 2004
    timestamp:          float = field(default_factory=time.time)


# ── Simulator ─────────────────────────────────────────────────────────────────

class TurbineSimulator:
    """
    Runs GE 3.6-100 physics in a daemon thread at DT-second intervals.
    All public methods are thread-safe.
    """

    DT = 0.5    # physics update interval (seconds)
    GEARBOX_RATIO = 97.0   # GE 3.6-100 gearbox ratio (approx)

    def __init__(self, turbine_id: str = "ARKLOW_GE3600_T1", seed: int = 3):
        self.turbine_id = turbine_id
        self._rng       = random.Random(seed)
        self._state     = GE3600State()
        self._lock      = threading.Lock()
        self._running   = False

        # Ornstein-Uhlenbeck wind state
        self._wind_ou_state = ARKLOW_WIND_MEAN_MS

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        t = threading.Thread(target=self._run_loop, daemon=True,
                              name=f"Physics-{self.turbine_id}")
        t.start()
        logger.info(f"[{self.turbine_id}] GE 3.6-100 physics started")

    def stop(self):
        self._running = False

    # ── Command interface ──────────────────────────────────────────────────────

    def cmd_pitch(self, angle_deg: float) -> bool:
        """
        Command collective blade pitch to angle_deg (0–90°).
        Returns False if blocked by E-STOP.
        """
        angle_deg = max(PITCH_MIN_DEG, min(PITCH_MAX_DEG, float(angle_deg)))
        with self._lock:
            if self._state.e_stop_active:
                logger.warning(f"[{self.turbine_id}] Pitch cmd rejected — E-STOP active")
                return False
            self._state.pitch_demand_deg = angle_deg
        logger.info(f"[{self.turbine_id}] Pitch demand → {angle_deg:.1f}°")
        return True

    def cmd_yaw(self, bearing_deg: float) -> bool:
        """Command nacelle to bearing bearing_deg (0–359°)."""
        bearing_deg = float(bearing_deg) % 360.0
        with self._lock:
            if self._state.e_stop_active:
                return False
            self._state.yaw_demand_deg = bearing_deg
        logger.info(f"[{self.turbine_id}] Yaw demand → {bearing_deg:.1f}°")
        return True

    def cmd_estop(self):
        """Activate emergency stop — feathers all blades immediately."""
        with self._lock:
            self._state.e_stop_active      = True
            self._state.pitch_demand_deg   = PITCH_MAX_DEG
            self._state.fault_code         = 999
            self._state.fault_description  = "EMERGENCY STOP ACTIVATED"
        logger.critical(f"[{self.turbine_id}] EMERGENCY STOP")

    def cmd_clear_estop(self):
        with self._lock:
            self._state.e_stop_active     = False
            self._state.fault_code        = 0
            self._state.fault_description = ""
        logger.info(f"[{self.turbine_id}] E-STOP cleared")

    def cmd_curtail(self, level_pct: float):
        """Curtail output to level_pct percent of rated (0–100)."""
        with self._lock:
            self._state.curtailment_pct = max(0.0, min(100.0, level_pct))

    # ── State access ──────────────────────────────────────────────────────────

    def get_state(self) -> GE3600State:
        with self._lock:
            return copy.copy(self._state)

    def sensor_snapshot(self) -> dict:
        """Return a flat dict of all sensor readings for transmission."""
        s = self.get_state()
        return {
            "turbine_id":             self.turbine_id,
            "farm":                   "ARKLOW_BANK_WIND_PARK",
            "timestamp":              s.timestamp,
            # Wind
            "wind_speed_ms":          round(s.wind_speed_ms, 2),
            "wind_dir_deg":           round(s.wind_dir_deg, 1),
            "wind_turbulence_ms":     round(s.wind_turbulence_ms, 3),
            # Drivetrain
            "rotor_rpm":              round(s.rotor_rpm, 2),
            "gen_rpm":                round(s.gen_rpm, 1),
            "power_kw":               round(s.power_kw, 1),
            "reactive_power_kvar":    round(s.reactive_power_kvar, 1),
            # Actuator positions
            "pitch_actual_deg":       round(s.pitch_actual_deg, 2),
            "pitch_demand_deg":       round(s.pitch_demand_deg, 2),
            "yaw_actual_deg":         round(s.yaw_actual_deg, 1),
            "yaw_demand_deg":         round(s.yaw_demand_deg, 1),
            # Temperatures
            "temp_nacelle_c":         round(s.temp_nacelle_c, 1),
            "temp_gearbox_c":         round(s.temp_gearbox_c, 1),
            "temp_main_bearing_c":    round(s.temp_main_bearing_c, 1),
            "temp_generator_c":       round(s.temp_generator_c, 1),
            "temp_blade_root_c":      round(s.temp_blade_root_c, 1),
            "temp_ambient_c":         round(s.temp_ambient_c, 1),
            # Structural
            "tower_fore_aft_mm":      round(s.tower_motion.fore_aft_mm, 2),
            "tower_side_side_mm":     round(s.tower_motion.side_side_mm, 2),
            "tower_tilt_mdeg":        round(s.tower_motion.tilt_mdeg, 3),
            "blade_vibration_hz":     round(s.blade_vibration_hz, 3),
            # Condition monitoring
            "gearbox_vibration_g":    round(s.gearbox_vibration_g, 3),
            "bearing_grease_temp_c":  round(s.main_bearing_grease_temp_c, 1),
            # Status
            "curtailment_pct":        round(s.curtailment_pct, 1),
            "fault_code":             s.fault_code,
            "fault_description":      s.fault_description,
            "e_stop_active":          s.e_stop_active,
            "op_hours":               round(s.op_hours, 1),
        }

    # ── Physics loop ──────────────────────────────────────────────────────────

    def _run_loop(self):
        while self._running:
            try:
                self._step()
            except Exception as exc:
                logger.error(f"[{self.turbine_id}] Physics error: {exc}")
            time.sleep(self.DT)

    def _step(self):
        dt = self.DT
        with self._lock:
            s = self._state

            # ── Wind: Ornstein-Uhlenbeck at Arklow mean ───────────────────
            # θ=0.08 (slightly slower reversion than generic), σ=0.45
            dv = 0.08 * (ARKLOW_WIND_MEAN_MS - self._wind_ou_state) * dt
            dv += 0.45 * math.sqrt(dt) * self._rng.gauss(0, 1)
            self._wind_ou_state = max(0.0, min(35.0, self._wind_ou_state + dv))
            s.wind_speed_ms = self._wind_ou_state

            # Turbulent component (10-second bandwidth)
            s.wind_turbulence_ms = (
                ARKLOW_TI * s.wind_speed_ms * self._rng.gauss(0, 1)
            )

            # Wind direction: slow random walk around SW
            s.wind_dir_deg = (
                s.wind_dir_deg + self._rng.gauss(0, 0.15) * dt
            ) % 360.0

            # ── Servo dynamics ────────────────────────────────────────────
            s.pitch_actual_deg = self._servo(
                s.pitch_actual_deg, s.pitch_demand_deg, PITCH_RATE_DEG_S, dt)
            s.yaw_actual_deg   = self._servo(
                s.yaw_actual_deg,   s.yaw_demand_deg,   YAW_RATE_DEG_S,   dt)

            # ── Aerodynamics ──────────────────────────────────────────────
            yaw_err   = abs(self._angle_delta(s.wind_dir_deg, s.yaw_actual_deg))
            cos_yaw   = max(0.0, math.cos(math.radians(yaw_err)))
            v_eff     = s.wind_speed_ms * cos_yaw

            cp        = self._power_coeff(s.pitch_actual_deg, s.rotor_rpm, v_eff)
            p_aero_kw = 0.5 * AIR_DENSITY * SWEPT_AREA_M2 * v_eff**3 * cp / 1000.0

            if s.e_stop_active or v_eff < CUT_IN_MS or v_eff > CUT_OUT_MS:
                p_aero_kw = 0.0

            # ── Rotor RPM dynamics ────────────────────────────────────────
            rpm_tgt = self._rated_rpm(v_eff, s.pitch_actual_deg)
            tau_rpm = 10.0   # rotor inertia time constant (s) — GE 3.6 is heavier
            s.rotor_rpm += (rpm_tgt - s.rotor_rpm) * dt / tau_rpm
            s.rotor_rpm  = max(0.0, min(MAX_RPM, s.rotor_rpm))
            s.gen_rpm    = s.rotor_rpm * self.GEARBOX_RATIO

            # ── Power ─────────────────────────────────────────────────────
            raw_kw   = min(p_aero_kw * GEN_EFFICIENCY, RATED_POWER_KW)
            s.power_kw = raw_kw * (s.curtailment_pct / 100.0) \
                         if s.rotor_rpm > 2.0 else 0.0
            s.reactive_power_kvar = s.power_kw * math.tan(
                math.acos(POWER_FACTOR)
            )

            # ── Temperature model ─────────────────────────────────────────
            lf = s.power_kw / RATED_POWER_KW
            s.temp_gearbox_c        = 48 + 28 * lf + self._rng.gauss(0, 0.08)
            s.temp_main_bearing_c   = 35 + 18 * lf + self._rng.gauss(0, 0.05)
            s.temp_generator_c      = 62 + 32 * lf + self._rng.gauss(0, 0.12)
            s.temp_nacelle_c        = s.temp_ambient_c + 10 + 15 * lf \
                                      + self._rng.gauss(0, 0.1)
            s.temp_blade_root_c     = s.temp_ambient_c + 2 + 4 * lf  \
                                      + self._rng.gauss(0, 0.15)

            # ── Condition monitoring ──────────────────────────────────────
            # Gearbox vibration increases slightly with load
            s.gearbox_vibration_g   = 0.08 + 0.12 * lf + abs(
                self._rng.gauss(0, 0.005))
            s.main_bearing_grease_temp_c = 38 + 10 * lf \
                                           + self._rng.gauss(0, 0.05)

            # ── Monopile tower motion (Irish Sea) ─────────────────────────
            # Monopile is much stiffer than floating — amplitudes in mm/mdeg
            t = time.time()
            s.tower_motion.fore_aft_mm  = (
                IRISH_SEA_HS * 8.0 * math.sin(2*math.pi*t / IRISH_SEA_TP)
            )
            s.tower_motion.side_side_mm = (
                IRISH_SEA_HS * 5.0 * math.sin(2*math.pi*t / (IRISH_SEA_TP*1.2) + 0.8)
            )
            s.tower_motion.tilt_mdeg    = (
                IRISH_SEA_HS * 3.0 * math.sin(2*math.pi*t / (IRISH_SEA_TP*0.85) + 1.5)
            )

            # Blade vibration (1P frequency at current RPM)
            s.blade_vibration_hz = s.rotor_rpm / 60.0 if s.rotor_rpm > 1 else 0.0

            # ── Stochastic faults ─────────────────────────────────────────
            if not s.e_stop_active and self._rng.random() < 0.00008:
                fault_catalogue = [
                    (201, "Gearbox oil temperature high"),
                    (202, "Main bearing vibration elevated"),
                    (301, "Generator winding temperature warning"),
                    (401, "Pitch actuator hydraulic pressure low"),
                    (501, "Yaw brake wear indicator"),
                    (102, "Blade leading-edge erosion alert"),
                ]
                code, desc = self._rng.choice(fault_catalogue)
                s.fault_code        = code
                s.fault_description = desc
                logger.warning(f"[{self.turbine_id}] FAULT {code}: {desc}")
            elif s.fault_code != 0 and self._rng.random() < 0.008:
                s.fault_code        = 0
                s.fault_description = ""

            s.op_hours  += dt / 3600.0
            s.timestamp  = time.time()

    # ── Physics helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _servo(actual: float, demand: float, max_rate: float, dt: float) -> float:
        delta    = demand - actual
        max_step = max_rate * dt
        if abs(delta) <= max_step:
            return demand
        return actual + math.copysign(max_step, delta)

    @staticmethod
    def _angle_delta(a: float, b: float) -> float:
        d = (a - b) % 360.0
        return d - 360.0 if d > 180.0 else d

    @staticmethod
    def _power_coeff(pitch_deg: float, rpm: float, wind_ms: float) -> float:
        """
        Simplified Cp surface for GE 3.6-100.
        Cp depends on tip-speed ratio λ and pitch angle β.
        Peak Cp=0.46 at λ_opt=7.5, pitch≈0°.
        """
        if wind_ms < 0.5 or rpm < 0.5:
            return 0.0
        omega = rpm * 2.0 * math.pi / 60.0
        lam   = omega * ROTOR_RADIUS_M / wind_ms
        pitch_factor = max(0.0, 1.0 - (pitch_deg / PITCH_MAX_DEG) ** 0.9)
        lam_factor   = max(0.0, 1.0 - abs(lam - LAMBDA_OPT) / LAMBDA_OPT)
        return MAX_CP * pitch_factor * lam_factor

    @staticmethod
    def _rated_rpm(wind_ms: float, pitch_deg: float) -> float:
        if wind_ms < CUT_IN_MS:
            return 0.0
        if wind_ms >= RATED_WIND_MS:
            return RATED_RPM
        frac = (wind_ms - CUT_IN_MS) / (RATED_WIND_MS - CUT_IN_MS)
        return RATED_RPM * math.sqrt(frac) * max(0.0, 1.0 - pitch_deg / PITCH_MAX_DEG)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    sim = TurbineSimulator("ARKLOW_GE3600_T1")
    sim.start()
    time.sleep(1.0)

    print("=== Arklow Bank GE 3.6-100 Physics Self-Test ===\n")
    for i in range(8):
        snap = sim.sensor_snapshot()
        print(
            f"t={i:2d} | Wind={snap['wind_speed_ms']:>5.2f} m/s  "
            f"RPM={snap['rotor_rpm']:>5.2f}  "
            f"Power={snap['power_kw']:>7.1f} kW  "
            f"Pitch={snap['pitch_actual_deg']:>5.1f}°  "
            f"Gbx={snap['temp_gearbox_c']:.1f}°C"
        )
        time.sleep(0.6)
    sim.stop()