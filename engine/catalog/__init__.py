"""Composable catalog of military hardware used in the wargame.

Designed to plug into a sensor-fusion layer later — sensors carry their
modality (radar / EO / IR / SIGINT / sonar) and characteristics, weapons
carry per-target Pkill tables, platforms compose 0+ sensors and 0+ weapons,
bases are fixed installations that spawn / repair platforms.

Read order if you're new:
    sensors.py   -> Sensor
    weapons.py   -> Weapon
    platforms.py -> Platform (composes Sensor + Weapon)
    bases.py     -> Base     (composes Sensor + Weapon, hosts Platforms)
"""
from engine.catalog.bases import BASES, Base
from engine.catalog.platforms import PLATFORMS, Platform
from engine.catalog.sensors import SENSORS, Sensor
from engine.catalog.weapons import WEAPONS, Weapon

__all__ = [
    "Sensor", "SENSORS",
    "Weapon", "WEAPONS",
    "Platform", "PLATFORMS",
    "Base", "BASES",
]
