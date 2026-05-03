"""Controller abstraction + registry.

A Controller takes a GameState and a side, and returns a list of Orders for
that side's units plus a metadata dict (reasoning, telemetry, lessons used,
etc).

The registry maps a string identifier (sent over the wire from the frontend)
to a Controller factory. The 'manual' kind is a sentinel — it never produces
orders; the server skips AI for that side.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Tuple

from engine.orders import Order
from engine.state import GameState


# Result type: (orders, meta) where meta carries free-form telemetry shown in
# the reasoning panel and logged for post-game reflection.
ControllerResult = Tuple[List[Order], Dict[str, Any]]


class Controller(ABC):
    """A side controller. Subclasses pick an action for every controllable
    unit on `side` given the live game state."""

    kind: str  # short id for the registry; subclass must set

    @abstractmethod
    async def decide(self, state: GameState, side: str) -> ControllerResult:
        ...


# Module-level registry. Use register_controller() to add at import time.
_REGISTRY: Dict[str, Callable[[], Controller]] = {}


def register_controller(kind: str) -> Callable[[type], type]:
    """Class decorator: registers the Controller subclass under `kind`."""
    def deco(cls: type) -> type:
        _REGISTRY[kind] = cls  # type: ignore[assignment]
        cls.kind = kind  # type: ignore[attr-defined]
        return cls
    return deco


def get_controller(kind: str) -> Controller:
    """Instantiate a controller by kind. 'manual' raises since it never runs."""
    if kind == "manual":
        raise ValueError("manual controller is a sentinel — server should not call it")
    factory = _REGISTRY.get(kind)
    if factory is None:
        raise KeyError(f"unknown controller kind {kind!r}. Known: {sorted(_REGISTRY)}")
    return factory()


def known_kinds() -> List[str]:
    """Available non-manual kinds (for the UI selector)."""
    return ["manual"] + sorted(_REGISTRY.keys())
