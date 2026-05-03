"""Sensor catalog.

Each Sensor describes a detection capability that gets bolted onto a
Platform or Base. The fusion layer (future) will iterate over a side's
attached sensors and union their detections; sensors track modality so
the fusion layer can apply rules like "radar emits and is itself
detectable by SIGINT", "EO is daylight-only", "sonar only sees sea
units", etc.

Pure data — no game-state coupling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Modality = Literal["radar", "eo", "ir", "sigint", "sonar"]
TargetDomain = Literal["land", "air", "sea", "amphib", "fixed"]


@dataclass(frozen=True)
class Sensor:
    key: str
    display: str
    modality: Modality
    range: int                # detection radius in hexes
    los_required: bool = False
    detects_stealth: bool = True
    # If non-empty, only detects targets whose `domain` is in this list.
    target_domains: tuple[TargetDomain, ...] = ()
    # Active emitter? Active sensors are themselves detectable by SIGINT.
    emits: bool = False
    notes: str = ""


SENSORS: dict[str, Sensor] = {
    # ---- Radar (active) ----
    "radar_aam": Sensor(
        key="radar_aam", display="Airborne AESA Radar", modality="radar",
        range=2, emits=True, detects_stealth=False,
        notes="Fighter-class AESA. Stealth targets reduce effective range.",
    ),
    "radar_aew": Sensor(
        key="radar_aew", display="AEW Radar", modality="radar",
        range=4, emits=True, detects_stealth=False,
        notes="Airborne early warning (KJ-500-class).",
    ),
    "radar_ddg": Sensor(
        key="radar_ddg", display="Shipborne AESA (SPY-6 / 346A)", modality="radar",
        range=3, emits=True, detects_stealth=True,
        notes="Naval AAW radar, doubles as missile-defense cue.",
    ),
    "radar_sam": Sensor(
        key="radar_sam", display="SAM Acquisition Radar", modality="radar",
        range=2, emits=True, los_required=True, detects_stealth=False,
        notes="Battery-organic FCR. Blocked by terrain.",
    ),
    # ---- EO / IR (passive) ----
    "eo_uav": Sensor(
        key="eo_uav", display="UAV EO/IR Ball", modality="eo",
        range=1,
        notes="MQ-9-class stareball, daylight + IR.",
    ),
    "eo_recon": Sensor(
        key="eo_recon", display="HALE Recon EO/IR + SAR", modality="eo",
        range=2,
        notes="Dedicated ISR platform (RQ-4 / WZ-7-class).",
    ),
    "tank_optics": Sensor(
        key="tank_optics", display="AFV Optics + FLIR", modality="ir",
        range=0, los_required=True,
        notes="Crew optics; own hex only.",
    ),
    "mk1_eyeball": Sensor(
        key="mk1_eyeball", display="Infantry Visual / Binos", modality="eo",
        range=0, los_required=True,
        notes="Squad-level observation; own hex only.",
    ),
    # ---- SIGINT / passive RF ----
    "esm_ddg": Sensor(
        key="esm_ddg", display="Shipborne ESM (SLQ-32)", modality="sigint",
        range=4,
        notes="Detects active emitters at long range, passive.",
    ),
    "rwr": Sensor(
        key="rwr", display="Aircraft RWR", modality="sigint",
        range=2,
        notes="Warns when illuminated; passive.",
    ),
    # ---- Sonar ----
    "sonar_hull": Sensor(
        key="sonar_hull", display="Hull Sonar (SQQ-89-class)", modality="sonar",
        range=1, target_domains=("sea",),
        notes="Naval ASW; sea-domain only.",
    ),
}


def get(key: str) -> Sensor:
    if key not in SENSORS:
        raise KeyError(f"Unknown sensor {key!r}. Known: {sorted(SENSORS)}")
    return SENSORS[key]
