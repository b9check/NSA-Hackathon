"""
Multi-sensor track fusion.

Each cycle:
  - Predict existing tracks with a constant-velocity Kalman model
  - Associate observations to predicted positions with range/bearing gates
  - Update matched tracks with range+bearing fixes and bearing-only EKF updates
  - Decay unmatched track confidence by tactical mobility category

Joint = existence × P(class | exists). Class confidence is always ≤ existence,
because joint probability is what's exposed to the UI.

Track identity is preserved across cycles by spatial association — but a
track's confidence numbers are recomputed from each cycle's observations
only. Static targets with stable observation geometry produce stable
confidence numbers.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from sensors import (
    Observation, SensorType, Position, Classification,
    EmissionType, ALL_CLASSES, RCS_PROFILES,
)


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------

EMITTER_LIBRARY: dict = {
    EmissionType.FIRE_CONTROL: {
        Classification.SAM_BATTERY:    0.70,
        Classification.RADAR_STATION:  0.25,
        Classification.AIRCRAFT:       0.03,
        Classification.COMMAND_POST:   0.01,
        Classification.VEHICLE_CONVOY: 0.005,
        Classification.INFANTRY:       0.005,
    },
    EmissionType.SEARCH_RADAR: {
        Classification.RADAR_STATION:  0.65,
        Classification.SAM_BATTERY:    0.20,
        Classification.AIRCRAFT:       0.08,
        Classification.COMMAND_POST:   0.06,
        Classification.VEHICLE_CONVOY: 0.005,
        Classification.INFANTRY:       0.005,
    },
    EmissionType.COMMS_HF: {
        Classification.COMMAND_POST:   0.55,
        Classification.RADAR_STATION:  0.15,
        Classification.SAM_BATTERY:    0.10,
        Classification.VEHICLE_CONVOY: 0.10,
        Classification.AIRCRAFT:       0.08,
        Classification.INFANTRY:       0.02,
    },
    EmissionType.COMMS_VHF: {
        Classification.VEHICLE_CONVOY: 0.55,
        Classification.COMMAND_POST:   0.25,
        Classification.INFANTRY:       0.08,
        Classification.SAM_BATTERY:    0.05,
        Classification.RADAR_STATION:  0.04,
        Classification.AIRCRAFT:       0.03,
    },
    EmissionType.JAMMING: {
        Classification.AIRCRAFT:       0.50,
        Classification.SAM_BATTERY:    0.20,
        Classification.RADAR_STATION:  0.15,
        Classification.COMMAND_POST:   0.10,
        Classification.VEHICLE_CONVOY: 0.04,
        Classification.INFANTRY:       0.01,
    },
    EmissionType.DATA_LINK: {
        Classification.AIRCRAFT:       0.45,
        Classification.COMMAND_POST:   0.30,
        Classification.SAM_BATTERY:    0.10,
        Classification.RADAR_STATION:  0.10,
        Classification.VEHICLE_CONVOY: 0.04,
        Classification.INFANTRY:       0.01,
    },
}


# Map class -> tactical category. This is intentionally not just air/land/sea:
# the category drives mobility and stale-track persistence behavior.
TACTICAL_CATEGORY_BY_CLASS = {
    Classification.AIRCRAFT:       "Air",
    Classification.SAM_BATTERY:    "Fixed Site",
    Classification.RADAR_STATION:  "Fixed Site",
    Classification.COMMAND_POST:   "C2",
    Classification.VEHICLE_CONVOY: "Mobile Ground",
    Classification.INFANTRY:       "Mobile Ground",
}

MOBILITY_MODEL_BY_CATEGORY = {
    "Fixed Site": {
        "position_q_km2": 0.50,
        "velocity_q_km2": 0.01,
        "confidence_decay": 0.015,
    },
    "C2": {
        "position_q_km2": 1.00,
        "velocity_q_km2": 0.03,
        "confidence_decay": 0.020,
    },
    "Mobile Ground": {
        "position_q_km2": 5.00,
        "velocity_q_km2": 0.15,
        "confidence_decay": 0.060,
    },
    "Air": {
        "position_q_km2": 200.00,
        "velocity_q_km2": 5.00,
        "confidence_decay": 0.180,
    },
}

# How much each sensor TYPE contributes to existence confidence when it
# detects a target. Independent observations from different types compose
# multiplicatively: existence = 1 - Π (1 - q_i).
SENSOR_QUALITY = {
    SensorType.RADAR:  0.70,
    SensorType.CAMERA: 0.55,
    SensorType.SIGINT: 0.55,
}

RCS_MEASUREMENT_LOG_SIGMA = 0.4


def rcs_likelihood(rcs: float) -> dict:
    log_rcs = math.log(max(rcs, 0.01))
    out = {}
    for c, (mu, sigma) in RCS_PROFILES.items():
        sigma_eff = math.sqrt(sigma * sigma + RCS_MEASUREMENT_LOG_SIGMA * RCS_MEASUREMENT_LOG_SIGMA)
        z = (log_rcs - mu) / sigma_eff
        out[c] = math.exp(-0.5 * z * z) / sigma_eff
    return out


def uniform_class_prior() -> dict:
    p = 1.0 / len(ALL_CLASSES)
    return {c: p for c in ALL_CLASSES}


def softened(d: dict, floor: float) -> dict:
    out = {c: max(d.get(c, 0.0), floor) for c in ALL_CLASSES}
    s = sum(out.values())
    return {c: v / s for c, v in out.items()}


# ---------------------------------------------------------------------------
# 2x2 matrix helpers
# ---------------------------------------------------------------------------

def m2_inv(m):
    a, b, c, d = m[0][0], m[0][1], m[1][0], m[1][1]
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    return [[d / det, -b / det], [-c / det, a / det]]

def m2_add(a, b):
    return [[a[0][0]+b[0][0], a[0][1]+b[0][1]], [a[1][0]+b[1][0], a[1][1]+b[1][1]]]

def m2_eigen(m):
    """Return (eig_max, eig_min, angle_of_eig_max_rad) for 2x2 symmetric m."""
    a, b, d = m[0][0], m[0][1], m[1][1]
    trace = a + d
    det = a * d - b * b
    disc = max(0.0, trace * trace / 4 - det)
    sqrt_disc = math.sqrt(disc)
    eig1 = trace / 2 + sqrt_disc
    eig2 = max(0.0, trace / 2 - sqrt_disc)
    if abs(b) > 1e-9:
        angle = math.atan2(eig1 - a, b)
    elif a >= d:
        angle = 0.0
    else:
        angle = math.pi / 2
    return eig1, eig2, angle


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest absolute angular difference in degrees."""
    return abs(((a - b) + 180.0) % 360.0 - 180.0)


def m2_quad_form(inv_m, vx: float, vy: float) -> float:
    return vx * (inv_m[0][0] * vx + inv_m[0][1] * vy) + vy * (inv_m[1][0] * vx + inv_m[1][1] * vy)


def category_probs_from_class_probs(class_probs: dict) -> dict:
    out = {name: 0.0 for name in MOBILITY_MODEL_BY_CATEGORY}
    for c, p in class_probs.items():
        out[TACTICAL_CATEGORY_BY_CLASS[c]] += p
    return out


def mobility_params_from_class_probs(class_probs: dict) -> dict:
    category_probs = category_probs_from_class_probs(class_probs)
    params = {"position_q_km2": 0.0, "velocity_q_km2": 0.0, "confidence_decay": 0.0}
    for category, p in category_probs.items():
        model = MOBILITY_MODEL_BY_CATEGORY[category]
        for k in params:
            params[k] += p * model[k]
    return params


def m4_identity() -> list:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def m4_vec_mul(m: list, v: list) -> list:
    return [sum(m[i][j] * v[j] for j in range(4)) for i in range(4)]


def m4_mul(a: list, b: list) -> list:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def m4_transpose(m: list) -> list:
    return [[m[j][i] for j in range(4)] for i in range(4)]


def m4_add(a: list, b: list) -> list:
    return [[a[i][j] + b[i][j] for j in range(4)] for i in range(4)]


def _initial_state_cov(position_cov: list) -> list:
    return [
        [position_cov[0][0], position_cov[0][1], 0.0, 0.0],
        [position_cov[1][0], position_cov[1][1], 0.0, 0.0],
        [0.0, 0.0, 4.0, 0.0],
        [0.0, 0.0, 0.0, 4.0],
    ]


def _position_cov_from_state_cov(state_cov: list) -> list:
    return [
        [max(state_cov[0][0], 1e-6), state_cov[0][1]],
        [state_cov[1][0], max(state_cov[1][1], 1e-6)],
    ]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Track:
    """
    Persistent identity for a target.

    Per-sensor measurement summaries (smoothed evidence, RCS estimate, position)
    are kept across cycles — these are equivalent to "what we currently believe
    each sensor is reporting" and converge to stable values for static targets.

    Existence is always recomputed from THIS cycle's observations only — so
    if a sensor stops seeing the target, existence drops immediately.

    Class probability is computed from current (smoothed) per-sensor evidence —
    no multiplicative buildup over time.
    """
    track_id: str

    # Last known state
    position: Position
    position_cov: list  # 2x2 covariance, km²
    state_vector: list = field(default_factory=list)  # [x, y, vx, vy]
    state_cov: list = field(default_factory=list)     # 4x4 covariance

    last_observed_time: float = 0.0

    # Smoothed per-sensor evidence: sensor_id -> P(class | this sensor's recent observations)
    sensor_evidence: dict = field(default_factory=dict)
    rcs_estimate: Optional[float] = None
    rcs_samples: int = 0
    emission_type_counts: dict = field(default_factory=dict)
    emission_evidence: dict = field(default_factory=dict)

    # Snapshot for this cycle:
    currently_observed: bool = False
    existence: float = 0.0
    track_confidence: float = 0.0
    cycles_since_last_seen: float = 0.0
    class_probs_conditional: dict = field(default_factory=uniform_class_prior)
    contributing_sensor_ids: list = field(default_factory=list)
    last_observations: list = field(default_factory=list)

    @property
    def dominant_emission_type(self) -> Optional[EmissionType]:
        if not self.emission_type_counts:
            return None
        return max(self.emission_type_counts.items(), key=lambda kv: kv[1])[0]

    @property
    def joint_class_probs(self) -> dict:
        return {c: self.track_confidence * p for c, p in self.class_probs_conditional.items()}

    @property
    def type_probs(self) -> dict:
        """Joint tactical category probabilities — sum to track confidence."""
        return {k: v * self.track_confidence for k, v in self.category_probs_conditional.items()}

    @property
    def category_probs_conditional(self) -> dict:
        return category_probs_from_class_probs(self.class_probs_conditional)

    @property
    def leading_category(self) -> tuple:
        return max(self.category_probs_conditional.items(), key=lambda kv: kv[1])

    def ellipse_params(self) -> tuple:
        eig_max, eig_min, angle = m2_eigen(self.position_cov)
        return math.sqrt(eig_max), math.sqrt(eig_min), math.degrees(angle)

    def to_dict(self) -> dict:
        major, minor, orient = self.ellipse_params()
        joint = self.joint_class_probs
        types = self.type_probs
        leading_cls, leading_p_joint = max(joint.items(), key=lambda kv: kv[1])
        leading_category, leading_category_p_cond = self.leading_category
        leading_category_p = leading_category_p_cond * self.track_confidence
        return {
            "track_id": self.track_id,
            "position": {"x": self.position.x, "y": self.position.y},
            "velocity": {
                "vx": self.state_vector[2] if self.state_vector else 0.0,
                "vy": self.state_vector[3] if self.state_vector else 0.0,
            },
            "position_uncertainty": {
                "ellipse_major_km": major,
                "ellipse_minor_km": minor,
                "orientation_deg": orient,
            },
            "rcs_estimate": self.rcs_estimate,
            "currently_observed": self.currently_observed,
            "currently_detected": self.currently_observed,
            "last_observed_time": self.last_observed_time,
            "cycles_since_last_seen": self.cycles_since_last_seen,
            "track_confidence": self.track_confidence,
            "existence": self.existence,
            "class_joint_probs": {c.value: p for c, p in joint.items()},
            "class_probs_conditional": {c.value: p for c, p in self.class_probs_conditional.items()},
            "category_joint_probs": types,
            "category_probs_conditional": self.category_probs_conditional,
            "type_joint_probs": types,
            "leading_class": leading_cls.value,
            "leading_class_prob": leading_p_joint,
            "leading_category": leading_category,
            "leading_category_prob": leading_category_p,
            "leading_type": leading_category,
            "leading_type_prob": leading_category_p,
            "dominant_emission_type": self.dominant_emission_type.value if self.dominant_emission_type else None,
            "contributing_sensor_ids": list(self.contributing_sensor_ids),
            "last_observations": [
                {
                    "sensor_id": r["sensor_id"],
                    "sensor_type": r["sensor_type"],
                    "bearing": r["bearing"],
                    "range_km": r.get("range_km"),
                    "rcs": r.get("rcs"),
                    "emission_type": r.get("emission_type"),
                    "association_confidence": r.get("association_confidence"),
                    "detection_probability": r.get("detection_probability"),
                }
                for r in self.last_observations
            ],
        }


# ---------------------------------------------------------------------------
# Snapshot computation
# ---------------------------------------------------------------------------

def _meas_position(obs: Observation) -> tuple:
    b = math.radians(obs.bearing)
    return (
        obs.sensor_position.x + obs.range_km * math.sin(b),
        obs.sensor_position.y + obs.range_km * math.cos(b),
    )

def _range_bearing_meas_cov(obs: Observation) -> list:
    b = math.radians(obs.bearing)
    sin_b, cos_b = math.sin(b), math.cos(b)
    sigma_r = obs.range_error or max(0.5, 0.01 * obs.range_km)
    sigma_t = obs.range_km * math.radians(obs.bearing_error or 1.5)
    sr2, st2 = sigma_r * sigma_r, sigma_t * sigma_t
    # cov = sr² (u_r u_r^T) + st² (u_t u_t^T) where u_r=(sin_b, cos_b), u_t=(cos_b, -sin_b)
    return [
        [sr2*sin_b*sin_b + st2*cos_b*cos_b, sr2*sin_b*cos_b - st2*cos_b*sin_b],
        [sr2*sin_b*cos_b - st2*cos_b*sin_b, sr2*cos_b*cos_b + st2*sin_b*sin_b],
    ]


def compute_position_snapshot(observations: list, init_guess: Optional[tuple]) -> tuple:
    """
    Compute (position, covariance) from this cycle's observations alone.
    Uses Fisher information from each measurement, linearized around init_guess.
    Returns (Position, 2x2_cov) or (None, None) if not estimable.
    """
    rb_observations = [o for o in observations if o.range_km is not None and o.bearing is not None]
    bo_observations = [o for o in observations if o.range_km is None and o.bearing is not None]

    if not rb_observations and len(bo_observations) < 2:
        return None, None  # underdetermined

    # Establish initial guess
    if init_guess is None:
        if rb_observations:
            # Average of direct position measurements
            x_avg = sum(_meas_position(o)[0] for o in rb_observations) / len(rb_observations)
            y_avg = sum(_meas_position(o)[1] for o in rb_observations) / len(rb_observations)
            init_guess = (x_avg, y_avg)
        else:
            # Triangulate two bearing-only sensors to get a starting point
            init_guess = _triangulate_two(bo_observations[0], bo_observations[1])
            if init_guess is None:
                return None, None

    # Fixed-point iteration: linearize bearing-only obs around current guess,
    # accumulate Fisher information, take ML step, repeat to convergence.
    px, py = init_guess
    for _ in range(8):
        info = [[0.0, 0.0], [0.0, 0.0]]    # total Fisher information
        info_pos = [0.0, 0.0]              # accumulator: info @ position contribution

        for o in rb_observations:
            R = _range_bearing_meas_cov(o)
            R_inv = m2_inv(R)
            if R_inv is None:
                continue
            mx, my = _meas_position(o)
            info = m2_add(info, R_inv)
            info_pos[0] += R_inv[0][0]*mx + R_inv[0][1]*my
            info_pos[1] += R_inv[1][0]*mx + R_inv[1][1]*my

        for o in bo_observations:
            sx, sy = o.sensor_position.x, o.sensor_position.y
            dx, dy = px - sx, py - sy
            r2 = dx*dx + dy*dy
            if r2 < 0.01:
                continue
            # Linearized observation: bearing ≈ atan2(dx, dy) + H @ (Δp)
            H = [dy / r2, -dx / r2]
            sigma = math.radians(o.bearing_error or 4.0)
            w = 1.0 / (sigma * sigma)
            # Information contribution: H^T w H (2x2)
            info[0][0] += H[0]*H[0]*w
            info[0][1] += H[0]*H[1]*w
            info[1][0] += H[1]*H[0]*w
            info[1][1] += H[1]*H[1]*w
            # Pseudo-measurement contribution to info_pos:
            #   info_pos += H^T * w * (z_meas - h(p_lin) + H @ p_lin)
            # where h(p_lin) = atan2(dx, dy)
            predicted = math.atan2(dx, dy)
            measured = math.radians(o.bearing)
            innov = ((measured - predicted + math.pi) % (2*math.pi)) - math.pi
            # Contribution: H^T * w * (innov + H @ p_lin)
            scalar = w * (innov + H[0]*px + H[1]*py)
            info_pos[0] += H[0] * scalar
            info_pos[1] += H[1] * scalar

        cov = m2_inv(info)
        if cov is None:
            return None, None

        new_px = cov[0][0]*info_pos[0] + cov[0][1]*info_pos[1]
        new_py = cov[1][0]*info_pos[0] + cov[1][1]*info_pos[1]
        delta = math.sqrt((new_px-px)**2 + (new_py-py)**2)
        px, py = new_px, new_py
        if delta < 0.001:
            break

    return Position(px, py), cov


def _triangulate_two(a: Observation, b: Observation) -> Optional[tuple]:
    ax, ay = a.sensor_position.x, a.sensor_position.y
    bx, by = b.sensor_position.x, b.sensor_position.y
    ba, bb = math.radians(a.bearing), math.radians(b.bearing)
    dxa, dya = math.sin(ba), math.cos(ba)
    dxb, dyb = math.sin(bb), math.cos(bb)
    denom = dxa*dyb - dya*dxb
    if abs(denom) < 1e-9:
        return None
    t = ((bx - ax)*dyb - (by - ay)*dxb) / denom
    if t < 0:
        return None
    x = ax + t*dxa
    y = ay + t*dya
    if abs(x) > 1000 or abs(y) > 1000:
        return None
    return (x, y)


def compute_existence(observations: list) -> float:
    """
    1 - Π (1 - q_effective) over unique sensors observing this target.
    q_effective combines sensor quality, detection probability at range, association
    confidence, and whether the current snapshot has an actual range fix.
    """
    if not observations:
        return 0.0
    seen_sensor_ids = set()
    survival = 1.0
    has_range_fix = any(o.range_km is not None for o in observations)
    for o in observations:
        if o.sensor_id in seen_sensor_ids:
            continue
        seen_sensor_ids.add(o.sensor_id)
        base_q = SENSOR_QUALITY[o.sensor_type]
        det_p = o.detection_probability if o.detection_probability is not None else 0.8
        assoc = max(0.0, min(1.0, getattr(o, "association_confidence", 1.0)))
        localization_factor = 1.0 if has_range_fix else 0.45
        # Normalize: at peak detection probability ~0.95, q_effective = base_q;
        # at marginal 0.20, q_effective is about 0.21 * base_q before association.
        q_effective = base_q * min(1.0, det_p / 0.95) * assoc * localization_factor
        survival *= (1.0 - q_effective)
    return 1.0 - survival


def compute_class_probs(observations: list, floor: float = 0.03) -> dict:
    """
    Bayesian update from a uniform prior using each observation's class likelihood.
    Returns conditional P(class | exists).
    """
    posterior = uniform_class_prior()
    used_sensors = set()
    for o in observations:
        if o.sensor_id in used_sensors:
            continue  # only one contribution per sensor (avoid double-counting)
        used_sensors.add(o.sensor_id)

        contributions = []
        if o.class_likelihood:
            contributions.append(o.class_likelihood)
        if o.emission_type and o.emission_type in EMITTER_LIBRARY:
            contributions.append(softened(EMITTER_LIBRARY[o.emission_type], floor=0.05))
        if o.rcs is not None:
            contributions.append(rcs_likelihood(o.rcs))
        if not contributions:
            continue

        combined = {c: 1.0 for c in ALL_CLASSES}
        for lik in contributions:
            for c in ALL_CLASSES:
                combined[c] *= max(lik.get(c, 1e-6), 1e-6)
        s = sum(combined.values())
        if s <= 0:
            continue
        per_sensor = {c: max(v / s, 0.02) for c, v in combined.items()}
        s2 = sum(per_sensor.values())
        per_sensor = {c: v / s2 for c, v in per_sensor.items()}

        for c in ALL_CLASSES:
            posterior[c] *= per_sensor[c]

    total = sum(posterior.values())
    if total <= 0:
        return uniform_class_prior()
    normalized = {c: v / total for c, v in posterior.items()}
    floored = {c: max(v, floor) for c, v in normalized.items()}
    s = sum(floored.values())
    return {c: v / s for c, v in floored.items()}


# ---------------------------------------------------------------------------
# Fusion engine
# ---------------------------------------------------------------------------

class FusionEngine:
    def __init__(self):
        self.tracks: list = []
        self._next_id = 1
        self.position_gate_chi2 = 16.0          # 4-sigma 2D gate for range+bearing fixes
        self.cluster_merge_chi2 = 16.0
        self.bearing_gate_sigma = 2.0           # normalized by each sensor's bearing error
        self.min_bearing_association = 0.25
        self.stale_after_cycles = 30
        self.drop_confidence_threshold = 0.08
        self.range_margin_pct = 0.10           # allow 10% slop on max-range gate
        self.new_track_min_existence = 0.15
        self.id_smoothing = 0.3
        self.evidence_stale_after_cycles = 5   # drop per-sensor evidence after this long without refresh
        self._last_cycle_time = 0.0

    def _new_id(self) -> str:
        tid = f"TRK-{self._next_id:04d}"
        self._next_id += 1
        return tid

    def _ensure_track_state(self, tr: Track):
        if not tr.state_vector:
            tr.state_vector = [tr.position.x, tr.position.y, 0.0, 0.0]
        if not tr.state_cov:
            tr.state_cov = _initial_state_cov(tr.position_cov)
        tr.position = Position(tr.state_vector[0], tr.state_vector[1])
        tr.position_cov = _position_cov_from_state_cov(tr.state_cov)

    def _sync_track_from_state(self, tr: Track):
        tr.position = Position(tr.state_vector[0], tr.state_vector[1])
        tr.position_cov = _position_cov_from_state_cov(tr.state_cov)

    def _symmetrize_state_cov(self, cov: list) -> list:
        out = [[0.0 for _ in range(4)] for _ in range(4)]
        for i in range(4):
            for j in range(4):
                out[i][j] = 0.5 * (cov[i][j] + cov[j][i])
        for i in range(4):
            out[i][i] = max(out[i][i], 1e-6)
        return out

    def _predict_track(self, tr: Track, dt: float):
        self._ensure_track_state(tr)
        dt = max(0.0, dt)
        if dt <= 0:
            return

        params = mobility_params_from_class_probs(tr.class_probs_conditional)
        category_probs = category_probs_from_class_probs(tr.class_probs_conditional)
        motion_factor = (
            category_probs.get("Mobile Ground", 0.0) +
            category_probs.get("Air", 0.0) +
            0.35 * category_probs.get("C2", 0.0)
        )
        motion_factor = max(0.0, min(1.0, motion_factor))
        velocity_retention = max(0.10, min(1.0, motion_factor + 0.50 * category_probs.get("C2", 0.0)))
        f = [
            [1.0, 0.0, dt * motion_factor, 0.0],
            [0.0, 1.0, 0.0, dt * motion_factor],
            [0.0, 0.0, velocity_retention, 0.0],
            [0.0, 0.0, 0.0, velocity_retention],
        ]
        q_pos = params["position_q_km2"] * dt
        q_vel = params["velocity_q_km2"] * dt
        q = [
            [q_pos, 0.0, 0.0, 0.0],
            [0.0, q_pos, 0.0, 0.0],
            [0.0, 0.0, q_vel, 0.0],
            [0.0, 0.0, 0.0, q_vel],
        ]
        tr.state_vector = m4_vec_mul(f, tr.state_vector)
        tr.state_cov = self._symmetrize_state_cov(m4_add(m4_mul(m4_mul(f, tr.state_cov), m4_transpose(f)), q))
        self._sync_track_from_state(tr)

    def _kalman_update_position(self, tr: Track, mx: float, my: float, r: list):
        self._ensure_track_state(tr)
        p = tr.state_cov
        s = [
            [p[0][0] + r[0][0], p[0][1] + r[0][1]],
            [p[1][0] + r[1][0], p[1][1] + r[1][1]],
        ]
        s_inv = m2_inv(s)
        if s_inv is None:
            return
        innovation = [mx - tr.state_vector[0], my - tr.state_vector[1]]
        k = []
        for i in range(4):
            k.append([
                p[i][0] * s_inv[0][0] + p[i][1] * s_inv[1][0],
                p[i][0] * s_inv[0][1] + p[i][1] * s_inv[1][1],
            ])
        for i in range(4):
            tr.state_vector[i] += k[i][0] * innovation[0] + k[i][1] * innovation[1]
        kh = [[0.0 for _ in range(4)] for _ in range(4)]
        for i in range(4):
            kh[i][0] = k[i][0]
            kh[i][1] = k[i][1]
        i_minus_kh = m4_identity()
        for i in range(4):
            for j in range(4):
                i_minus_kh[i][j] -= kh[i][j]
        tr.state_cov = self._symmetrize_state_cov(m4_mul(i_minus_kh, p))
        self._sync_track_from_state(tr)

    def _ekf_update_bearing(self, tr: Track, o: Observation):
        self._ensure_track_state(tr)
        sx, sy = o.sensor_position.x, o.sensor_position.y
        dx = tr.state_vector[0] - sx
        dy = tr.state_vector[1] - sy
        r2 = dx * dx + dy * dy
        if r2 < 0.01:
            return
        predicted = math.atan2(dx, dy)
        measured = math.radians(o.bearing)
        innovation = ((measured - predicted + math.pi) % (2 * math.pi)) - math.pi
        h = [dy / r2, -dx / r2, 0.0, 0.0]
        sigma = math.radians(o.bearing_error or 4.0)
        p = tr.state_cov
        ph = [sum(p[i][j] * h[j] for j in range(4)) for i in range(4)]
        s = sum(h[i] * ph[i] for i in range(4)) + sigma * sigma
        if s <= 1e-12:
            return
        k = [v / s for v in ph]
        for i in range(4):
            tr.state_vector[i] += k[i] * innovation
        i_minus_kh = m4_identity()
        for i in range(4):
            for j in range(4):
                i_minus_kh[i][j] -= k[i] * h[j]
        tr.state_cov = self._symmetrize_state_cov(m4_mul(i_minus_kh, p))
        self._sync_track_from_state(tr)

    def _update_track_state_from_observations(self, tr: Track, observations: list):
        self._ensure_track_state(tr)
        for o in observations:
            if o.range_km is None or o.bearing is None:
                continue
            mx, my = _meas_position(o)
            self._kalman_update_position(tr, mx, my, _range_bearing_meas_cov(o))
        for o in observations:
            if o.range_km is not None or o.bearing is None:
                continue
            self._ekf_update_bearing(tr, o)

    def _missed_track_decay(self, tr: Track, dt: float) -> float:
        params = mobility_params_from_class_probs(tr.class_probs_conditional)
        return math.exp(-params["confidence_decay"] * max(0.0, dt))

    def _mark_track_unobserved(self, tr: Track, current_time: float, dt: float):
        tr.currently_observed = False
        tr.cycles_since_last_seen = current_time - tr.last_observed_time
        tr.track_confidence *= self._missed_track_decay(tr, dt)
        tr.existence = tr.track_confidence
        tr.contributing_sensor_ids = []
        tr.last_observations = []

    def process_observations(self, observations: list, current_time: float):
        dt = max(1.0, current_time - self._last_cycle_time) if self.tracks else 1.0
        for tr in self.tracks:
            self._predict_track(tr, dt)

        # Cluster observations -> assign each to an existing track or to a new cluster
        clusters = self._cluster(observations, current_time)

        observed_ids = set()
        track_by_id = {t.track_id: t for t in self.tracks}
        for tid, obs_list in clusters["existing"].items():
            tr = track_by_id[tid]
            if obs_list:
                self._refresh_track(tr, obs_list, current_time)
                observed_ids.add(tr.track_id)
        for obs_list in clusters["new"]:
            tr = self._create_track(obs_list, current_time)
            if tr:
                self.tracks.append(tr)
                observed_ids.add(tr.track_id)

        # Mark un-observed tracks as stale; remove ones too old
        survivors = []
        for tr in self.tracks:
            if tr.track_id in observed_ids:
                tr.cycles_since_last_seen = current_time - tr.last_observed_time
                survivors.append(tr)
                continue
            self._mark_track_unobserved(tr, current_time, dt)
            cycles_since_seen = current_time - tr.last_observed_time
            if cycles_since_seen <= self.stale_after_cycles and tr.track_confidence >= self.drop_confidence_threshold:
                survivors.append(tr)
            # else dropped
        self.tracks = survivors
        self._last_cycle_time = current_time

    # ---- clustering ----------------------------------------------------

    def _range_feasible(self, o: Observation, pos: Position) -> bool:
        dist = math.hypot(pos.x - o.sensor_position.x, pos.y - o.sensor_position.y)
        return dist <= o.sensor_max_range_km * (1.0 + self.range_margin_pct)

    def _emission_compatible_with_track(self, tr: Track, o: Observation) -> bool:
        if o.emission_type is None:
            return True
        dominant = tr.dominant_emission_type
        if dominant is None:
            return True
        return dominant == o.emission_type

    def _emission_compatible_with_cluster(self, seed: list, o: Observation) -> bool:
        if o.emission_type is None:
            return True
        for prev in seed:
            if prev.emission_type is not None and prev.emission_type != o.emission_type:
                return False
        return True

    def _emitter_compatibility_from_distribution(self, class_dist: dict, emission_type: EmissionType) -> float:
        lib = EMITTER_LIBRARY.get(emission_type)
        if not lib:
            return 1.0
        score = sum(class_dist.get(c, 0.0) * lib.get(c, 0.0) for c in ALL_CLASSES)
        max_score = max(lib.values())
        if max_score <= 0:
            return 1.0
        return max(0.0, min(1.0, score / max_score))

    def _emitter_track_adjustment(self, tr: Track, o: Observation) -> Optional[tuple]:
        if o.emission_type is None:
            return 0.0, 1.0
        dominant = tr.dominant_emission_type
        if dominant == o.emission_type:
            return -2.0, 1.0
        compat = self._emitter_compatibility_from_distribution(
            tr.class_probs_conditional, o.emission_type
        )
        penalty = 0.5 + 4.0 * (1.0 - compat)
        assoc_factor = max(0.20, compat)
        return penalty, assoc_factor

    def _emitter_seed_adjustment(self, seed: list, o: Observation, require_compat: bool = False) -> Optional[tuple]:
        if o.emission_type is None:
            return 0.0, 1.0
        if any(prev.emission_type == o.emission_type for prev in seed):
            return -2.0, 1.0
        class_dist = compute_class_probs(seed, floor=0.01)
        compat = self._emitter_compatibility_from_distribution(class_dist, o.emission_type)
        if require_compat and compat < 0.30:
            return None
        penalty = 4.0 * (1.0 - compat)
        assoc_factor = max(0.20, compat)
        return penalty, assoc_factor

    def _cluster_position_cov(self, seed: list) -> tuple:
        pos, cov = compute_position_snapshot(seed, init_guess=None)
        if pos is None:
            return None, None
        return pos, cov

    def _range_track_score(self, o: Observation, tr: Track) -> Optional[tuple]:
        if not self._emission_compatible_with_track(tr, o):
            return None
        mx, my = _meas_position(o)
        R = _range_bearing_meas_cov(o)
        cov = m2_add(R, tr.position_cov)
        cov_inv = m2_inv(cov)
        if cov_inv is None:
            return None
        chi2 = m2_quad_form(cov_inv, mx - tr.position.x, my - tr.position.y)
        if chi2 > self.position_gate_chi2:
            return None
        assoc = max(0.20, math.exp(-0.125 * chi2))
        return chi2, assoc

    def _bearing_track_score(self, o: Observation, tr: Track) -> Optional[tuple]:
        if not self._emission_compatible_with_track(tr, o):
            return None
        if not self._range_feasible(o, tr.position):
            return None
        predicted = o.sensor_position.bearing_to(tr.position)
        sigma = max(0.1, o.bearing_error or 4.0)
        z = angle_diff_deg(o.bearing, predicted) / sigma
        if z > self.bearing_gate_sigma:
            return None
        adjustment = self._emitter_track_adjustment(tr, o)
        if adjustment is None:
            return None
        emitter_penalty, emitter_assoc = adjustment
        assoc = math.exp(-0.5 * z * z) * emitter_assoc
        if assoc < self.min_bearing_association:
            return None
        return z * z + emitter_penalty, assoc

    def _range_seed_score(self, o: Observation, seed: list) -> Optional[tuple]:
        if any(prev.sensor_id == o.sensor_id for prev in seed):
            return None
        if not self._emission_compatible_with_cluster(seed, o):
            return None
        pos, cov = self._cluster_position_cov(seed)
        if pos is None:
            return None
        mx, my = _meas_position(o)
        candidate_cov = m2_add(_range_bearing_meas_cov(o), cov)
        candidate_inv = m2_inv(candidate_cov)
        if candidate_inv is None:
            return None
        chi2 = m2_quad_form(candidate_inv, mx - pos.x, my - pos.y)
        if chi2 > self.cluster_merge_chi2:
            return None
        assoc = max(0.20, math.exp(-0.125 * chi2))
        return chi2, assoc

    def _bearing_seed_score(self, o: Observation, seed: list) -> Optional[tuple]:
        if any(prev.sensor_id == o.sensor_id for prev in seed):
            return None
        if not self._emission_compatible_with_cluster(seed, o):
            return None
        pos, _ = self._cluster_position_cov(seed)
        if pos is None:
            return None
        if not self._range_feasible(o, pos):
            return None
        predicted = o.sensor_position.bearing_to(pos)
        sigma = max(0.1, o.bearing_error or 4.0)
        z = angle_diff_deg(o.bearing, predicted) / sigma
        if z > self.bearing_gate_sigma:
            return None
        adjustment = self._emitter_seed_adjustment(seed, o)
        if adjustment is None:
            return None
        emitter_penalty, emitter_assoc = adjustment
        assoc = math.exp(-0.5 * z * z) * emitter_assoc
        if assoc < self.min_bearing_association:
            return None
        return z * z + emitter_penalty, assoc

    def _bearing_only_seed_score(self, o: Observation, seed: list) -> Optional[tuple]:
        if any(prev.sensor_id == o.sensor_id for prev in seed):
            return None
        if any(prev.range_km is not None for prev in seed):
            return None
        if not self._emission_compatible_with_cluster(seed, o):
            return None

        candidate = seed + [o]
        if len(candidate) < 2:
            return None
        pos, cov = compute_position_snapshot(candidate, init_guess=None)
        if pos is None or cov is None:
            return None
        for obs in candidate:
            if not self._range_feasible(obs, pos):
                return None

        eig_max, _, _ = m2_eigen(cov)
        major_sigma = math.sqrt(max(0.0, eig_max))
        geometry_factor = 1.0 / (1.0 + major_sigma / 25.0)
        adjustment = self._emitter_seed_adjustment(seed, o, require_compat=True)
        if adjustment is None:
            return None
        emitter_penalty, emitter_assoc = adjustment
        assoc = max(0.10, min(0.70, 0.80 * geometry_factor * emitter_assoc))
        if assoc < self.min_bearing_association:
            return None
        return 1.0 / max(assoc, 1e-6) + emitter_penalty, assoc

    def _assign_existing_by_sensor(self, observations: list, existing: dict, scorer) -> set:
        assigned = set()
        by_sensor = {}
        for o in observations:
            by_sensor.setdefault(o.sensor_id, []).append(o)

        for sensor_obs in by_sensor.values():
            candidates = []
            for o in sensor_obs:
                for tr in self.tracks:
                    if any(prev.sensor_id == o.sensor_id for prev in existing[tr.track_id]):
                        continue
                    scored = scorer(o, tr)
                    if scored is None:
                        continue
                    cost, assoc = scored
                    candidates.append((cost, id(o), o, tr, assoc))

            used_obs = set()
            used_tracks = set()
            for _, oid, o, tr, assoc in sorted(candidates, key=lambda item: item[0]):
                if oid in used_obs or tr.track_id in used_tracks:
                    continue
                o.association_confidence = assoc
                existing[tr.track_id].append(o)
                used_obs.add(oid)
                used_tracks.add(tr.track_id)
                assigned.add(oid)

        return assigned

    def _cluster(self, observations: list, current_time: float):
        """
        Two-pass association with statistical gates and cannot-link constraints:
        - Range+bearing observations are matched by Mahalanobis distance.
        - Bearing-only observations are matched by normalized bearing residual
          and range feasibility.
        - Within each track in this cycle, at most one observation per sensor.
        - SIGINT emissions must be compatible with the candidate track/cluster.
        """
        existing = {tr.track_id: [] for tr in self.tracks}
        unmatched = []
        detected = [o for o in observations if o.detected and o.bearing is not None]

        rb_observations = [o for o in detected if o.range_km is not None]
        assigned_rb = self._assign_existing_by_sensor(
            rb_observations, existing, self._range_track_score
        )
        for o in rb_observations:
            if id(o) not in assigned_rb:
                unmatched.append(o)

        bo_observations = [o for o in detected if o.range_km is None]
        assigned_bo = self._assign_existing_by_sensor(
            bo_observations, existing, self._bearing_track_score
        )
        for o in bo_observations:
            if id(o) not in assigned_bo:
                unmatched.append(o)

        new_clusters = self._form_new_clusters(unmatched)
        return {"existing": existing, "new": new_clusters}

    def _form_new_clusters(self, observations: list) -> list:
        """
        Group unmatched observations into candidate clusters.
        Cannot-link rule: two observations from the same sensor in this cycle
        can never be in the same cluster — they're necessarily from distinct
        targets. Range gate: a bearing-only obs cannot attach to a cluster
        whose centroid lies beyond that sensor's max range.
        """
        seeds = []  # each: list of obs from distinct sensors

        # Range+bearing observations form seeds. Merge into existing seed only
        # if statistically compatible and the merging sensor is not already in the seed.
        for o in observations:
            if o.range_km is None or o.bearing is None:
                continue
            best_seed, best_cost, best_assoc = None, float("inf"), 1.0
            for s in seeds:
                scored = self._range_seed_score(o, s)
                if scored is None:
                    continue
                cost, assoc = scored
                if cost < best_cost:
                    best_seed, best_cost, best_assoc = s, cost, assoc
            if best_seed is not None:
                o.association_confidence = best_assoc
                best_seed.append(o)
            else:
                o.association_confidence = 1.0
                seeds.append([o])

        # Attach bearing-only obs to nearest seed by bearing AND range feasibility.
        for o in observations:
            if o.bearing is None or o.range_km is not None:
                continue
            best, best_cost, best_assoc = None, float("inf"), 1.0
            for s in seeds:
                if not any(prev.range_km is not None for prev in s):
                    continue
                scored = self._bearing_seed_score(o, s)
                if scored is None:
                    continue
                cost, assoc = scored
                if cost < best_cost:
                    best, best_cost, best_assoc = s, cost, assoc
            if best is not None:
                o.association_confidence = best_assoc
                best.append(o)
            else:
                # Couldn't find a range-bearing seed. Try a bearing-only cluster,
                # but keep its association confidence conservative.
                best_bo, best_bo_cost, best_bo_assoc = None, float("inf"), 0.0
                for s in seeds:
                    scored = self._bearing_only_seed_score(o, s)
                    if scored is None:
                        continue
                    cost, assoc = scored
                    if cost < best_bo_cost:
                        best_bo, best_bo_cost, best_bo_assoc = s, cost, assoc
                if best_bo is not None:
                    o.association_confidence = best_bo_assoc
                    for prev in best_bo:
                        prev.association_confidence = min(prev.association_confidence, best_bo_assoc)
                    best_bo.append(o)
                else:
                    o.association_confidence = 1.0
                    seeds.append([o])   # standalone - won't form a track without a partner

        return [s for s in seeds if len(s) >= 1]

    # ---- track creation / update --------------------------------------

    def _create_track(self, observations: list, current_time: float) -> Optional[Track]:
        pos, cov = compute_position_snapshot(observations, init_guess=None)
        if pos is None:
            return None
        if compute_existence(observations) < self.new_track_min_existence:
            return None
        tr = Track(
            track_id=self._new_id(),
            position=pos,
            position_cov=cov,
            state_vector=[pos.x, pos.y, 0.0, 0.0],
            state_cov=_initial_state_cov(cov),
            last_observed_time=current_time,
            track_confidence=compute_existence(observations),
            existence=compute_existence(observations),
        )
        self._refresh_track(tr, observations, current_time, update_kinematics=False)
        return tr

    def _refresh_track(self, tr: Track, observations: list, current_time: float, update_kinematics: bool = True):
        """Refresh a matched track with current observations and optional EKF update."""
        if not observations:
            tr.currently_observed = False
            return

        if update_kinematics:
            self._update_track_state_from_observations(tr, observations)

        # RCS smoothing in log space — measurement noise averages out for
        # static targets, so a static SAM converges to its true class RCS mean
        # rather than reflecting a single noisy sample.
        for o in observations:
            if o.rcs is None:
                continue
            if tr.rcs_estimate is None:
                tr.rcs_estimate = o.rcs
            else:
                k = tr.rcs_samples
                tr.rcs_estimate = math.exp(
                    (k * math.log(tr.rcs_estimate) + math.log(o.rcs)) / (k + 1)
                )
            tr.rcs_samples += 1

        # Update per-sensor smoothed class evidence. Each entry now carries a
        # last-refresh timestamp so contaminated old assignments expire.
        for o in observations:
            if o.emission_type is not None:
                tr.emission_evidence[o.sensor_id] = {
                    "emission_type": o.emission_type,
                    "last_refresh": current_time,
                }

            new_lik = self._observation_class_likelihood(o, tr.rcs_estimate)
            if new_lik is None:
                continue
            existing = tr.sensor_evidence.get(o.sensor_id)
            if existing is None:
                smoothed = dict(new_lik)
            else:
                # Geometric mean (smooth in log space)
                alpha = self.id_smoothing
                smoothed = {}
                for c in ALL_CLASSES:
                    log_old = math.log(max(existing.get("likelihood", {}).get(c, 1e-6), 1e-6))
                    log_new = math.log(max(new_lik.get(c, 1e-6), 1e-6))
                    smoothed[c] = math.exp((1 - alpha) * log_old + alpha * log_new)
                s = sum(smoothed.values())
                if s > 0:
                    smoothed = {c: v / s for c, v in smoothed.items()}
            tr.sensor_evidence[o.sensor_id] = {
                "likelihood": smoothed,
                "last_refresh": current_time,
            }

        # Drop stale per-sensor evidence (old contaminated assignments that no
        # longer get refreshed should not perpetually influence ID).
        stale_cutoff = current_time - self.evidence_stale_after_cycles
        tr.sensor_evidence = {
            sid: ev for sid, ev in tr.sensor_evidence.items()
            if ev.get("last_refresh", -1e9) >= stale_cutoff
        }
        tr.emission_evidence = {
            sid: ev for sid, ev in tr.emission_evidence.items()
            if ev.get("last_refresh", -1e9) >= stale_cutoff
        }
        tr.emission_type_counts = {}
        for ev in tr.emission_evidence.values():
            emission_type = ev.get("emission_type")
            if emission_type is not None:
                tr.emission_type_counts[emission_type] = tr.emission_type_counts.get(emission_type, 0) + 1

        # Compute class posterior from current (non-stale) per-sensor evidence.
        active_likelihoods = {
            sid: ev["likelihood"] for sid, ev in tr.sensor_evidence.items()
        }
        tr.class_probs_conditional = self._posterior_from_evidence(active_likelihoods)

        cycle_confidence = compute_existence(observations)
        tr.track_confidence = min(0.99, max(tr.track_confidence * 0.98, cycle_confidence))
        tr.existence = tr.track_confidence
        tr.last_observed_time = current_time
        tr.cycles_since_last_seen = 0.0
        tr.currently_observed = True
        tr.contributing_sensor_ids = sorted({o.sensor_id for o in observations})

        tr.last_observations = [
            {
                "sensor_id": o.sensor_id,
                "sensor_type": o.sensor_type.value,
                "bearing": o.bearing,
                "range_km": o.range_km,
                "rcs": o.rcs,
                "emission_type": o.emission_type.value if o.emission_type else None,
                "association_confidence": getattr(o, "association_confidence", 1.0),
                "detection_probability": o.detection_probability,
            }
            for o in observations
        ]

    def _observation_class_likelihood(self, o: Observation, smoothed_rcs: Optional[float]) -> Optional[dict]:
        """
        Build P(class | this sensor's observation), using the SMOOTHED RCS estimate
        rather than the raw single-cycle RCS sample. This makes ID stable for
        static targets (single noisy RCS readings don't dominate).
        """
        contributions = []
        if o.class_likelihood:
            contributions.append(o.class_likelihood)
        if o.emission_type and o.emission_type in EMITTER_LIBRARY:
            contributions.append(softened(EMITTER_LIBRARY[o.emission_type], floor=0.05))
        if o.rcs is not None and smoothed_rcs is not None:
            contributions.append(rcs_likelihood(smoothed_rcs))
        if not contributions:
            return None
        combined = {c: 1.0 for c in ALL_CLASSES}
        for lik in contributions:
            for c in ALL_CLASSES:
                combined[c] *= max(lik.get(c, 1e-6), 1e-6)
        s = sum(combined.values())
        if s <= 0:
            return None
        normed = {c: v / s for c, v in combined.items()}
        floored = {c: max(v, 0.02) for c, v in normed.items()}
        s2 = sum(floored.values())
        return {c: v / s2 for c, v in floored.items()}

    def _posterior_from_evidence(self, evidence_map: dict, floor: float = 0.03) -> dict:
        posterior = uniform_class_prior()
        for lik in evidence_map.values():
            for c in ALL_CLASSES:
                posterior[c] *= max(lik.get(c, 1e-6), 1e-6)
        total = sum(posterior.values())
        if total <= 0:
            return uniform_class_prior()
        normalized = {c: v / total for c, v in posterior.items()}
        floored = {c: max(v, floor) for c, v in normalized.items()}
        s = sum(floored.values())
        return {c: v / s for c, v in floored.items()}

    # ---- accessors ----------------------------------------------------

    def get_active_tracks(self) -> list:
        return [
            t for t in self.tracks
            if t.track_confidence >= self.drop_confidence_threshold and
            (t.currently_observed or (self._last_cycle_time - t.last_observed_time) <= self.stale_after_cycles)
        ]

    def get_all_tracks(self) -> list:
        return self.tracks
