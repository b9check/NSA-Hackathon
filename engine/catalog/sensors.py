"""Sensor catalog.

Two sensor concepts in the game:
  - passive view: always on, no emissions, short range
  - radar: toggle ON/OFF; emits when ON; longer range than passive

This catalog enumerates the few range tiers actually used by platforms.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Modality = Literal["radar", "eo", "ir", "sigint", "sonar"]
TargetDomain = Literal["land", "air", "sea", "amphib", "fixed"]


@dataclass(frozen=True)
class Sensor:
    key: str
    display: str
    modality: Modality
    range: int
    los_required: bool = False
    detects_stealth: bool = True
    target_domains: tuple[TargetDomain, ...] = ()
    emits: bool = False
    notes: str = ""


SENSORS: dict[str, Sensor] = {
    # ---- Passive view (always on, no emissions) ----
    "passive_1": Sensor(
        key="passive_1", display="Passive View", modality="eo", range=2,
        notes="Crew/sensor optics; sees nearby.",
    ),
    "passive_2": Sensor(
        key="passive_2", display="Passive View", modality="eo", range=3,
        notes="Wider passive sensor.",
    ),
    "passive_4": Sensor(
        key="passive_4", display="Wide Passive View", modality="eo", range=5,
        notes="Drone-class wide-area passive sensor.",
    ),
    # ---- Radar (toggle, emits when ON) ----
    "radar_3": Sensor(
        key="radar_3", display="Radar", modality="radar", range=4, emits=True,
        notes="Toggle: ON to see at range, exposes the unit at long range.",
    ),
    "radar_4": Sensor(
        key="radar_4", display="Long-Range Radar", modality="radar", range=5, emits=True,
        notes="Toggle: ON to see at range, exposes the unit at long range.",
    ),
}


def get(key: str) -> Sensor:
    if key not in SENSORS:
        raise KeyError(f"Unknown sensor {key!r}. Known: {sorted(SENSORS)}")
    return SENSORS[key]
