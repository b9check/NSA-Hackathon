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
    # Total time the unit can stay on station before mandatory RTB.
    # 0 = no logistics tracked (ground/sea units).
    endurance_minutes: int = 0

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
        key="infantry", display="Mechanized Infantry",
        domain="land", glyph="I",
        speed=2, hp=3, cost=20,
        sensors=("passive_2",),
        weapons=("inf_rifle",),
        role="Dismounted close combat with organic MANPADS for point air defense",
    ),
    "armor": Platform(
        key="armor", display="Armor (MBT)",
        domain="land", glyph="T",
        speed=4, hp=4, cost=50,
        sensors=("passive_1",),
        weapons=("armor_gun",),
        role="Main battle tank; shock and direct-fire maneuver in the close fight",
    ),
    "missile_launcher": Platform(
        key="missile_launcher", display="SAM Battery (Long-Range)",
        domain="land", glyph="S",
        speed=1, hp=3, cost=60,
        sensors=("passive_1", "radar_4"),
        weapons=("sam",),
        role="Long-range integrated air defense (TEL); requires search radar to engage beyond visual range",
    ),
    "scout_drone": Platform(
        key="scout_drone", display="ISR UAV (HALE)",
        domain="air", glyph="U",
        speed=5, hp=1, cost=20,
        scout_radius=4,
        sensors=("passive_2",),
        weapons=(),
        role="High-altitude long-endurance ISR; persistent wide-area surveillance and target cueing",
        endurance_minutes=1440,
    ),
    "strike_drone": Platform(
        key="strike_drone", display="Loitering Munition",
        domain="air", glyph="K",
        speed=4, hp=1, cost=10,
        sensors=("passive_1",),
        weapons=("kamikaze",),
        role="One-way attack munition; loiters then dives onto target, expended on engagement",
        endurance_minutes=60,
    ),
    "fighter": Platform(
        key="fighter", display="Multi-role Fighter (5th-gen)",
        domain="air", glyph="A",
        speed=5, hp=3, cost=100, stealth=True,
        sensors=("passive_1", "radar_3"),
        weapons=("aam_asm",),
        role="Combat air patrol, air defense, multi-role strike; LO airframe degrades when radar is radiating",
        endurance_minutes=240,
    ),
    "bomber": Platform(
        key="bomber", display="Strike Aircraft",
        domain="air", glyph="X",
        speed=3, hp=4, cost=80,
        sensors=("passive_1",),
        weapons=("bomb",),
        role="Long-range deep strike against fixed and high-value targets; limited self-defense",
        endurance_minutes=480,
    ),
    "destroyer": Platform(
        key="destroyer", display="AAW Destroyer (DDG)",
        domain="sea", glyph="N",
        speed=3, hp=5, cost=120,
        sensors=("passive_1", "radar_3"),
        weapons=("ship_battery",),
        role="Surface combatant providing area air defense, ASuW, and land-attack strike; pays radar emission tax",
    ),
}


def get(key: str) -> Platform:
    if key not in PLATFORMS:
        raise KeyError(f"Unknown platform {key!r}. Known: {sorted(PLATFORMS)}")
    return PLATFORMS[key]
