"""Platform catalog — 9 abstract unit types.

Both sides draw from the same roster; `side` is set per instance in the
scenario YAML, not on the platform itself. Numbers and behavior are
identical across sides — only the glyph color changes.

Composition: a platform attaches 0+ sensors and 0+ weapons by key.
SCOUT-capable platforms also declare a `scout_radius`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.catalog.sensors import SENSORS
from engine.catalog.weapons import WEAPONS


Domain = Literal["land", "air", "sea"]


@dataclass(frozen=True)
class Platform:
    key: str
    display: str
    domain: Domain
    glyph: str
    speed: int                   # max move per turn (hexes); 0 = stationary
    hp: int
    cost: int
    role: str = ""
    stealth: bool = False
    scout_radius: int = 0        # >0 means the platform supports SCOUT
    sensors: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()

    def summary_sensor_range(self) -> int:
        if not self.sensors:
            return 0
        return max(SENSORS[s].range for s in self.sensors)

    def summary_weapon_range(self) -> int:
        if not self.weapons:
            return 0
        return max(WEAPONS[w].range for w in self.weapons)


PLATFORMS: dict[str, Platform] = {
    "infantry": Platform(
        key="infantry", display="Infantry",
        domain="land", glyph="I",
        speed=2, hp=3, cost=20,
        sensors=("passive_2",),
        weapons=("inf_rifle",),
        role="Light infantry with rifle + MANPADS",
    ),
    "armor": Platform(
        key="armor", display="Armor",
        domain="land", glyph="T",
        speed=4, hp=4, cost=50,
        sensors=("passive_1",),
        weapons=("armor_gun",),
        role="Heavy ground striker; mobile but blind",
    ),
    "missile_launcher": Platform(
        key="missile_launcher", display="Missile Launcher",
        domain="land", glyph="S",
        speed=1, hp=3, cost=60,
        sensors=("passive_1", "radar_4"),
        weapons=("sam",),
        role="Mobile long-range AAW (TEL); needs radar ON to see beyond 1",
    ),
    "scout_drone": Platform(
        key="scout_drone", display="Scout Drone",
        domain="air", glyph="U",
        speed=5, hp=1, cost=20,
        scout_radius=4,
        sensors=("passive_2",),
        weapons=(),
        role="Eyes of the team. SCOUT reveals radius 4 around its hex",
    ),
    "strike_drone": Platform(
        key="strike_drone", display="Strike Drone",
        domain="air", glyph="K",
        speed=4, hp=1, cost=10,
        sensors=("passive_1",),
        weapons=("kamikaze",),
        role="One-shot kamikaze; dies after firing",
    ),
    "fighter": Platform(
        key="fighter", display="Fighter",
        domain="air", glyph="A",
        speed=5, hp=3, cost=100, stealth=True,
        sensors=("passive_1", "radar_3"),
        weapons=("aam_asm",),
        role="Fast multirole stealth; radar ON breaks stealth at long range",
    ),
    "bomber": Platform(
        key="bomber", display="Bomber",
        domain="air", glyph="X",
        speed=3, hp=4, cost=80,
        sensors=("passive_1",),
        weapons=("bomb",),
        role="Slow heavy striker; tiny magazine; no air-to-air",
    ),
    "destroyer": Platform(
        key="destroyer", display="Destroyer",
        domain="sea", glyph="N",
        speed=3, hp=5, cost=120,
        sensors=("passive_1", "radar_3"),
        weapons=("ship_battery",),
        role="Sea AAW + ASuW + land-attack; pays radar visibility tax",
    ),
}


def get(key: str) -> Platform:
    if key not in PLATFORMS:
        raise KeyError(f"Unknown platform {key!r}. Known: {sorted(PLATFORMS)}")
    return PLATFORMS[key]
