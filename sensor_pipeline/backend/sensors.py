"""
Sensor observation models for radar, camera, and SIGINT sensors.

Each sensor produces realistic feature-level observations:
  - Radar: bearing, range, RCS estimate, range-rate (Doppler)
  - Camera: bearing, class probability vector
  - SIGINT: bearing, emission type, signal strength

Each sensor has range-dependent detection probability and Gaussian noise.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class SensorType(Enum):
    RADAR = "radar"
    CAMERA = "camera"
    SIGINT = "sigint"


class Classification(Enum):
    SAM_BATTERY = "SAM Battery"
    RADAR_STATION = "Radar Station"
    COMMAND_POST = "Command Post"
    VEHICLE_CONVOY = "Vehicle Convoy"
    INFANTRY = "Infantry"
    AIRCRAFT = "Aircraft"


# All classes considered by the classifier (ordered for stable iteration)
ALL_CLASSES = [
    Classification.SAM_BATTERY,
    Classification.RADAR_STATION,
    Classification.COMMAND_POST,
    Classification.VEHICLE_CONVOY,
    Classification.INFANTRY,
    Classification.AIRCRAFT,
]


class EmissionType(Enum):
    SEARCH_RADAR = "Search Radar"
    FIRE_CONTROL = "Fire Control Radar"
    COMMS_HF = "HF Communications"
    COMMS_VHF = "VHF Communications"
    JAMMING = "Jamming"
    DATA_LINK = "Data Link"


# RCS distribution per platform class.
# (mean log-RCS in m^2, std-dev of log-RCS).
# Used both to generate noisy radar RCS estimates and to compute the likelihood
# P(rcs_observed | class) for Bayesian classification.
RCS_PROFILES = {
    Classification.SAM_BATTERY:    (math.log(20.0),  0.5),   # ~7-55 m²
    Classification.RADAR_STATION:  (math.log(100.0), 0.5),   # ~37-270 m²
    Classification.COMMAND_POST:   (math.log(40.0),  0.7),   # ~10-160 m²
    Classification.VEHICLE_CONVOY: (math.log(8.0),   0.6),   # ~2.5-25 m²
    Classification.INFANTRY:       (math.log(0.5),   0.7),   # ~0.13-2 m²
    Classification.AIRCRAFT:       (math.log(15.0),  1.0),   # ~2-110 m² (high variance)
}


@dataclass
class Position:
    x: float  # km east
    y: float  # km north

    def distance_to(self, other: "Position") -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def bearing_to(self, other: "Position") -> float:
        """Returns bearing in degrees (0=north, clockwise)."""
        dx = other.x - self.x
        dy = other.y - self.y
        return math.degrees(math.atan2(dx, dy)) % 360


@dataclass
class Target:
    position: Position
    classification: Classification
    emission_type: Optional[EmissionType] = None
    is_emitting: bool = False
    target_id: str = ""
    # Persistent true RCS, sampled once from the class distribution.
    # All radar observations of this target apply measurement noise around this fixed value
    # (real targets don't re-roll their RCS every observation).
    true_rcs: Optional[float] = None

    def get_or_init_rcs(self) -> float:
        if self.true_rcs is None:
            mu, sigma = RCS_PROFILES[self.classification]
            self.true_rcs = math.exp(random.gauss(mu, sigma))
        return self.true_rcs


@dataclass
class Observation:
    """A single sensor observation - partial and noisy."""
    sensor_type: SensorType
    sensor_id: str
    sensor_position: Position
    sensor_max_range_km: float        # required for range-aware association gating
    timestamp: float
    detected: bool = True

    # Bearing — produced by all sensor types
    bearing: Optional[float] = None
    bearing_error: Optional[float] = None  # 1-sigma in degrees

    # Radar-only
    range_km: Optional[float] = None
    range_error: Optional[float] = None
    rcs: Optional[float] = None  # m², noisy estimate
    range_rate: Optional[float] = None  # km/s (radial velocity, Doppler)

    # Camera-only — full distribution over classes (likelihood vector)
    class_likelihood: Optional[dict] = None  # dict[Classification, float], sums to 1

    # SIGINT-only
    emission_type: Optional[EmissionType] = None
    signal_strength: Optional[float] = None  # dB relative

    # Detection probability the sensor experienced at the observed range
    # (used to weight existence — marginal hits count for less than strong ones)
    detection_probability: Optional[float] = None

    # Set by fusion after association. A clean geometric/emission match should be
    # near 1; weak bearing-only coincidences should contribute much less.
    association_confidence: float = 1.0


class SensorBase:
    """Base class for all sensors."""

    def __init__(self, sensor_id: str, position: Position, max_range_km: float):
        self.sensor_id = sensor_id
        self.position = position
        self.max_range_km = max_range_km
        self.sensor_type: SensorType = SensorType.RADAR
        self.max_detection_probability = 0.90
        self.detection_knee = 0.70
        self.detection_steepness = 10.0

    def _detection_probability(self, distance_km: float) -> float:
        if distance_km > self.max_range_km:
            return 0.0
        ratio = distance_km / self.max_range_km
        prob = self.max_detection_probability / (
            1.0 + math.exp(self.detection_steepness * (ratio - self.detection_knee))
        )
        return max(0.0, min(self.max_detection_probability, prob))

    def _add_bearing_noise(self, true_bearing: float, sigma_deg: float) -> float:
        return (true_bearing + random.gauss(0, sigma_deg)) % 360

    def observe(self, target: Target, timestamp: float) -> Optional[Observation]:
        raise NotImplementedError


class RadarSensor(SensorBase):
    """
    Radar: bearing + range + RCS estimate + range-rate.
    No platform classification; RCS is the only ID signal it contributes.
    """

    def __init__(self, sensor_id: str, position: Position, max_range_km: float = 150.0):
        super().__init__(sensor_id, position, max_range_km)
        self.sensor_type = SensorType.RADAR
        self.bearing_sigma_deg = 1.5
        self.range_sigma_pct = 0.01
        self.rcs_log_sigma = 0.4  # noise on log-RCS measurement
        self.max_detection_probability = 0.98
        self.detection_knee = 0.86
        self.detection_steepness = 12.0

    def observe(self, target: Target, timestamp: float) -> Optional[Observation]:
        distance = self.position.distance_to(target.position)
        det_prob = self._detection_probability(distance)
        if random.random() > det_prob:
            return None

        true_bearing = self.position.bearing_to(target.position)
        noisy_bearing = self._add_bearing_noise(true_bearing, self.bearing_sigma_deg)

        range_sigma = max(0.05, distance * self.range_sigma_pct)
        noisy_range = distance + random.gauss(0, range_sigma)

        # Use persistent target RCS, apply measurement noise only.
        true_rcs = target.get_or_init_rcs()
        observed_rcs = true_rcs * math.exp(random.gauss(0, self.rcs_log_sigma))

        return Observation(
            sensor_type=SensorType.RADAR,
            sensor_id=self.sensor_id,
            sensor_position=self.position,
            sensor_max_range_km=self.max_range_km,
            timestamp=timestamp,
            detected=True,
            bearing=noisy_bearing,
            bearing_error=self.bearing_sigma_deg,
            range_km=max(0.1, noisy_range),
            range_error=range_sigma,
            rcs=observed_rcs,
            range_rate=0.0,
            detection_probability=det_prob,
        )


class CameraSensor(SensorBase):
    """
    Camera/EO-IR: bearing + class probability distribution.
    No range. Distribution is sharply peaked on truth at close range, flatter at far range.
    """

    def __init__(self, sensor_id: str, position: Position, max_range_km: float = 30.0):
        super().__init__(sensor_id, position, max_range_km)
        self.sensor_type = SensorType.CAMERA
        self.bearing_sigma_deg = 0.5
        self.max_detection_probability = 0.78
        self.detection_knee = 0.55
        self.detection_steepness = 11.0

    def _build_likelihood(self, true_class: Classification, distance_km: float) -> dict:
        """
        Build a P(class | image) likelihood vector for the camera.
        At close range: sharply peaked on truth.
        At max range: flat (uninformative).
        Adds confusion between visually similar classes.
        """
        # Logits scale: at 0% range = ~5 (very peaked), at 100% range = ~0 (uniform)
        ratio = min(1.0, distance_km / self.max_range_km)
        peak_strength = max(0.0, 5.0 * (1.0 - ratio))

        # Confusion pairs: classes the camera frequently mixes up
        confusion = {
            (Classification.SAM_BATTERY, Classification.RADAR_STATION): 0.6,
            (Classification.VEHICLE_CONVOY, Classification.COMMAND_POST): 0.5,
            (Classification.INFANTRY, Classification.VEHICLE_CONVOY): 0.4,
        }

        logits = {c: 0.0 for c in ALL_CLASSES}
        logits[true_class] = peak_strength
        for (a, b), strength in confusion.items():
            if true_class == a:
                logits[b] = peak_strength * strength
            elif true_class == b:
                logits[a] = peak_strength * strength

        # Add per-observation noise to each logit
        for c in logits:
            logits[c] += random.gauss(0, 0.4)

        # Softmax to probabilities
        max_logit = max(logits.values())
        exps = {c: math.exp(v - max_logit) for c, v in logits.items()}
        total = sum(exps.values())
        return {c: v / total for c, v in exps.items()}

    def observe(self, target: Target, timestamp: float) -> Optional[Observation]:
        distance = self.position.distance_to(target.position)
        det_prob = self._detection_probability(distance)
        if random.random() > det_prob:
            return None

        true_bearing = self.position.bearing_to(target.position)
        noisy_bearing = self._add_bearing_noise(true_bearing, self.bearing_sigma_deg)

        likelihood = self._build_likelihood(target.classification, distance)

        return Observation(
            sensor_type=SensorType.CAMERA,
            sensor_id=self.sensor_id,
            sensor_position=self.position,
            sensor_max_range_km=self.max_range_km,
            timestamp=timestamp,
            detected=True,
            bearing=noisy_bearing,
            bearing_error=self.bearing_sigma_deg,
            class_likelihood=likelihood,
            detection_probability=det_prob,
        )


class SIGINTSensor(SensorBase):
    """
    SIGINT: bearing + emission type + signal strength.
    Only detects emitting targets. Wide bearing error.
    """

    def __init__(self, sensor_id: str, position: Position, max_range_km: float = 200.0):
        super().__init__(sensor_id, position, max_range_km)
        self.sensor_type = SensorType.SIGINT
        self.bearing_sigma_deg = 4.0
        self.max_detection_probability = 0.80
        self.detection_knee = 0.68
        self.detection_steepness = 8.0

    def observe(self, target: Target, timestamp: float) -> Optional[Observation]:
        if not target.is_emitting or target.emission_type is None:
            return None

        distance = self.position.distance_to(target.position)
        det_prob = self._detection_probability(distance)
        if random.random() > det_prob:
            return None

        true_bearing = self.position.bearing_to(target.position)
        noisy_bearing = self._add_bearing_noise(true_bearing, self.bearing_sigma_deg)
        signal_strength = -20 * math.log10(max(1.0, distance)) + 60

        return Observation(
            sensor_type=SensorType.SIGINT,
            sensor_id=self.sensor_id,
            sensor_position=self.position,
            sensor_max_range_km=self.max_range_km,
            timestamp=timestamp,
            detected=True,
            bearing=noisy_bearing,
            bearing_error=self.bearing_sigma_deg,
            emission_type=target.emission_type,
            signal_strength=signal_strength,
            detection_probability=det_prob,
        )
