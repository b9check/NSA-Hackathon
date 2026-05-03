"""Back-compat shim — re-exports the new composable catalog.

Older code still imports `engine.units.CATALOG` / `engine.units.get` —
keep those names working. New code should import directly from
`engine.catalog`.
"""
from __future__ import annotations

from engine.catalog import PLATFORMS as CATALOG
from engine.catalog import Platform as UnitType
from engine.catalog.platforms import get

__all__ = ["CATALOG", "UnitType", "get"]
