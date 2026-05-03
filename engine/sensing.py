"""Multi-sensor fusion → per-side intel picture.

Bridges the discrete hex world with the probabilistic sensor model from
sensor_pipeline/. For every enemy entity, each side gets a `Contact` summarizing:
  - believed position (hex) + position uncertainty (hexes)
  - existence probability (P(real) — saturates with sensor diversity)
  - class probability distribution (over platform keys)
  - which sensors are contributing
  - dominant modality, for the UI

Behavior:
- A unit is "currently observed" by a side if any of that side's
  active sensors covers its hex (using stealth halving).
- Currently-observed contacts are refreshed every cycle. Class probabilities
  are recomputed from a uniform prior using current sensor likelihoods,
  smoothed against the previous estimate (per-sensor evidence smoothing
  prevents one noisy radar return from yanking ID around).
- Lost contacts ("ghosts") persist with growing position uncertainty and
  decaying existence. Decay rate depends on the contact's leading class —
  fixed sites barely move (slow decay), ground convoys moderate, aircraft
  fast.
- Position uncertainty is in hexes, not km. Tight (~0 hex) for radar+visual
  cross-confirmation; ~1.5 for radar alone; ~2.5 for SIGINT/passive only;
  larger for ghosts.

This is intentionally lightweight relative to the full Bayesian EKF in
sensor_pipeline/ — discrete grid, single uncertainty scalar, modality-keyed
likelihoods. The intent is: feel the *math* of fusion in the UI without
the apparatus that would matter on a continuous map.
"""
from __future__ import annotations

import math
from typing import Optional

from engine.catalog.platforms import PLATFORMS
from engine.hex import Hex, distance
from engine.state import Contact, GameState, UnitInstance, BaseInstance


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------

# Per-modality "evidence quality" for the existence saturating update.
# Independent sensor types compose: existence = 1 - Π (1 - q).
SENSOR_QUALITY_BY_MODALITY: dict[str, float] = {
    "radar":  0.70,   # active emission, high confidence detection at range
    "eo":     0.55,   # passive optics — visual confirmation
    "ir":     0.55,
    "sigint": 0.55,   # emission detection (only emitting targets)
    "sonar":  0.55,
}

# How much position uncertainty (in hexes) each modality contributes alone.
# The actual uncertainty for a contact = min over the modalities currently
# observing it. Multi-modal observations stack — see _combine_uncertainty.
UNCERTAINTY_HEXES_BY_MODALITY: dict[str, float] = {
    "radar":  0.7,    # best — measures both bearing and range
    "eo":     1.5,    # bearing only, but visually confirmed at the hex
    "ir":     1.5,
    "sigint": 2.5,    # bearing only, no range — broad
    "sonar":  1.0,
}

# Mobility class derived from platform domain + speed. Drives ghost decay
# and uncertainty growth: a fixed SAM ghost barely drifts; an aircraft
# ghost balloons fast.
MOBILITY_CLASS_BY_PLATFORM: dict[str, str] = {
    "infantry":         "ground_slow",
    "armor":            "ground_fast",
    "missile_launcher": "fixed_or_relocatable",
    "scout_drone":      "air",
    "strike_drone":     "air",
    "fighter":          "air",
    "bomber":           "air",
    "destroyer":        "ship",
}

# (existence_decay_per_turn, uncertainty_growth_hex_per_turn)
MOBILITY_DECAY: dict[str, tuple[float, float]] = {
    "ground_slow":          (0.05, 0.3),
    "ground_fast":          (0.07, 0.6),
    "fixed_or_relocatable": (0.02, 0.15),
    "air":                  (0.20, 2.0),
    "ship":                 (0.06, 0.5),
}

# Mapping: which platforms each sensor modality is well-suited to ID.
# Used to build a per-modality class likelihood vector. Higher = more
# confident this modality discriminates that platform.
# Values are relative weights (normalized later); 0 = uninformative.
MODALITY_CLASS_AFFINITY: dict[str, dict[str, float]] = {
    "radar": {
        "destroyer":        4.0,   # huge RCS, easy ID
        "missile_launcher": 2.5,
        "armor":            2.0,
        "bomber":           2.5,
        "fighter":          1.0,   # stealth — radar struggles
        "scout_drone":      1.0,
        "strike_drone":     0.8,
        "infantry":         0.5,
    },
    "eo": {
        "infantry":         3.0,
        "armor":            3.0,
        "missile_launcher": 3.0,
        "scout_drone":      2.0,
        "strike_drone":     2.0,
        "fighter":          2.0,
        "bomber":           2.0,
        "destroyer":        2.0,
    },
    "ir": {
        "infantry":         2.0,
        "armor":            3.0,
        "missile_launcher": 2.0,
        "scout_drone":      2.0,
        "strike_drone":     2.0,
        "fighter":          3.0,
        "bomber":           2.5,
        "destroyer":        2.5,
    },
    "sigint": {
        "missile_launcher": 4.0,   # FCR signature
        "destroyer":        2.5,   # search radar / data link
        "fighter":          2.0,
        "bomber":           1.0,
        "armor":            0.5,
        "infantry":         0.3,
        "scout_drone":      1.0,
        "strike_drone":     0.5,
    },
    "sonar": {
        "destroyer":        4.0,
    },
}

# Smoothing factor for per-sensor evidence — closer to 1 = very responsive
# (one good radar shot pegs ID); closer to 0 = takes many cycles to firm up.
ID_SMOOTHING = 0.55


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _platforms() -> list[str]:
    return list(PLATFORMS.keys())


def _uniform_class_prior() -> dict[str, float]:
    plats = _platforms()
    p = 1.0 / len(plats)
    return {k: p for k in plats}


def _normalize(d: dict[str, float], floor: float = 0.0) -> dict[str, float]:
    if floor > 0:
        d = {k: max(v, floor) for k, v in d.items()}
    s = sum(d.values())
    if s <= 0:
        return _uniform_class_prior()
    return {k: v / s for k, v in d.items()}


def _modality_likelihood(modality: str, true_class: str) -> dict[str, float]:
    """Per-sensor-cycle observation likelihood given the true platform.

    Concentrates probability on the true class scaled by this modality's
    affinity; spreads remaining probability over visually-confused classes.
    Floor prevents annihilation of any class.
    """
    affinity = MODALITY_CLASS_AFFINITY.get(modality, {})
    if not affinity:
        return _uniform_class_prior()

    # Affinity weight for true class (how well this modality identifies it)
    a = affinity.get(true_class, 0.5)

    # Build raw weights: peaked on truth, scaled by modality affinity for it,
    # background spread proportional to other classes' affinities.
    weights: dict[str, float] = {}
    for k in _platforms():
        base = affinity.get(k, 0.3)
        if k == true_class:
            weights[k] = base * (1.0 + 2.0 * a)
        else:
            weights[k] = base * 1.0
    return _normalize(weights, floor=0.02)


def _smooth_evidence(prev: Optional[dict[str, float]], new: dict[str, float]) -> dict[str, float]:
    """EMA-style smoothing of a per-sensor likelihood (in linear space)."""
    if prev is None:
        return dict(new)
    out = {}
    for k in _platforms():
        out[k] = (1.0 - ID_SMOOTHING) * prev.get(k, 1.0/len(_platforms())) + ID_SMOOTHING * new.get(k, 0.0)
    return _normalize(out)


def _combine_uncertainty(modalities: list[str]) -> float:
    """Combine multi-modal uncertainty: best modality dominates, but cross-confirmation tightens."""
    if not modalities:
        return 5.0
    bases = [UNCERTAINTY_HEXES_BY_MODALITY.get(m, 2.0) for m in modalities]
    best = min(bases)
    # Cross-modality bonus: each additional independent modality halves the residual
    extras = len(set(modalities)) - 1
    return best * (0.7 ** max(0, extras))


def _existence_from_observers(observers: list[tuple[str, str]]) -> float:
    """observers = [(sensor_id, modality), ...] currently sensing this entity.

    1 - Π (1 - q) where q is the modality's per-sensor evidence quality.
    Multi-modal coverage saturates toward 1 quickly; redundant same-modality
    coverage saturates more slowly (each shares the same q).
    """
    if not observers:
        return 0.0
    survival = 1.0
    for _sid, modality in observers:
        q = SENSOR_QUALITY_BY_MODALITY.get(modality, 0.5)
        survival *= (1.0 - q)
    return 1.0 - survival


# ---------------------------------------------------------------------------
# Coverage check (mirrors engine.resolve._is_visible but per-sensor)
# ---------------------------------------------------------------------------

def _sensor_observers_of(
    entity_col: int,
    entity_row: int,
    is_stealth: bool,
    side_units: list[UnitInstance],
    side_bases: list[BaseInstance],
) -> list[tuple[str, str]]:
    """Return [(observer_sensor_id, modality), ...] — every active AREA-COVERAGE
    sensor on `side_*` whose effective range covers (entity_col, entity_row).

    SIGINT is intentionally excluded here — SIGINT doesn't light up a region,
    it only catches emitters. Use `_emission_observers_of` for that pathway.
    """
    out: list[tuple[str, str]] = []
    target = Hex(entity_col, entity_row)
    for u in side_units:
        for s in u.sensors:
            if not s.is_active or s.modality == "sigint":
                continue
            eff = s.range // 2 if is_stealth else s.range
            if distance(Hex(u.col, u.row), target) <= eff:
                out.append((f"{u.id}::{s.key}", s.modality))
    for b in side_bases:
        for s in b.sensors:
            if not s.is_active or s.modality == "sigint":
                continue
            eff = s.range // 2 if is_stealth else s.range
            if distance(Hex(b.col, b.row), target) <= eff:
                out.append((f"{b.id}::{s.key}", s.modality))
    return out


def _emission_observers_of(
    entity: object,                                  # UnitInstance | BaseInstance
    side_units: list[UnitInstance],
    side_bases: list[BaseInstance],
) -> list[tuple[str, str]]:
    """Return [(observer_sigint_sensor_id, "sigint"), ...] — for each of my
    SIGINT sensors that has the entity in range AND the entity has at least
    one ACTIVE EMITTING sensor (e.g. radar ON).

    This is the entire reason emissions matter: lighting up a radar exposes
    you to enemy SIGINT receivers far beyond their normal area coverage.
    Stealth does NOT halve SIGINT range — emission strength dominates.
    """
    # Does the entity have any active emitter?
    ent_sensors = getattr(entity, "sensors", []) or []
    has_active_emitter = any(s.is_active and s.emits for s in ent_sensors)
    if not has_active_emitter:
        return []
    out: list[tuple[str, str]] = []
    target = Hex(entity.col, entity.row)
    for u in side_units:
        for s in u.sensors:
            if not s.is_active or s.modality != "sigint":
                continue
            if distance(Hex(u.col, u.row), target) <= s.range:
                out.append((f"{u.id}::{s.key}", "sigint"))
    for b in side_bases:
        for s in b.sensors:
            if not s.is_active or s.modality != "sigint":
                continue
            if distance(Hex(b.col, b.row), target) <= s.range:
                out.append((f"{b.id}::{s.key}", "sigint"))
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def update_contacts(state: GameState) -> None:
    """Recompute per-side contacts from current sensor coverage.

    Mutates state.contacts in place. Should be called at the end of
    `resolve_turn` — after movement, after combat, before the new turn.
    """
    # Index previous contacts by id for smoothing continuity
    prev_by_side_target: dict[str, dict[str, Contact]] = {
        "blue": {c.contact_id: c for c in state.contacts.get("blue", [])},
        "red":  {c.contact_id: c for c in state.contacts.get("red", [])},
    }

    new_contacts: dict[str, list[Contact]] = {"blue": [], "red": []}

    # Pre-bucket each side's units/bases for fast lookup
    blue_units = [u for u in state.units if u.side == "blue"]
    red_units  = [u for u in state.units if u.side == "red"]
    blue_bases = [b for b in state.bases if b.side == "blue"]
    red_bases  = [b for b in state.bases if b.side == "red"]

    # For each side, sense every enemy unit + base
    for observing_side, my_units, my_bases, enemies, enemy_bases in (
        ("blue", blue_units, blue_bases, red_units,  red_bases),
        ("red",  red_units,  red_bases,  blue_units, blue_bases),
    ):
        contacts: list[Contact] = []
        seen_ids: set[str] = set()

        for ent, kind in [(e, "unit") for e in enemies] + [(b, "base") for b in enemy_bases]:
            is_stealth = bool(getattr(ent, "stealth", False))
            area_obs = _sensor_observers_of(ent.col, ent.row, is_stealth, my_units, my_bases)
            emit_obs = _emission_observers_of(ent, my_units, my_bases)
            # Combine, dedup by sensor id (a sensor can't double-count itself).
            seen_sids = set()
            obs: list[tuple[str, str]] = []
            for sid, mod in area_obs + emit_obs:
                if sid in seen_sids:
                    continue
                seen_sids.add(sid)
                obs.append((sid, mod))

            if obs:
                # Currently observed: refresh fully
                seen_ids.add(ent.id)
                modalities = [m for _, m in obs]
                existence = _existence_from_observers(obs)
                uncertainty = _combine_uncertainty(modalities)

                # Per-modality smoothed class evidence: combine each modality's
                # likelihood, smooth against previous contact's posterior.
                prev = prev_by_side_target[observing_side].get(ent.id)
                # Build current observation posterior: product of modality likelihoods
                cur_lik = _uniform_class_prior()
                for modality in set(modalities):
                    mlik = _modality_likelihood(modality, ent.type)
                    for k in cur_lik:
                        cur_lik[k] *= mlik[k]
                cur_lik = _normalize(cur_lik, floor=0.005)
                # Smooth against previous posterior for stability
                prev_probs = prev.class_probs if prev else None
                smoothed = _smooth_evidence(prev_probs, cur_lik)

                dominant = max(set(modalities), key=lambda m:
                               len([1 for _, mm in obs if mm == m]) * SENSOR_QUALITY_BY_MODALITY.get(m, 0))

                contacts.append(Contact(
                    contact_id=ent.id,
                    target_kind=kind,
                    believed_col=ent.col,
                    believed_row=ent.row,
                    position_uncertainty_hexes=uncertainty,
                    existence=existence,
                    class_probs=smoothed,
                    last_refined_turn=state.turn,
                    currently_observed=True,
                    contributing_sensor_ids=[sid for sid, _ in obs],
                    dominant_modality=dominant,
                ))
            else:
                # Not observed this cycle — promote previous contact (ghost) if any.
                prev = prev_by_side_target[observing_side].get(ent.id)
                if prev is None:
                    continue
                turns_since = max(1, state.turn - prev.last_refined_turn)
                # Decay rate keyed on the GHOST's own leading class hypothesis
                # (we don't actually know the truth — we go on what we believed)
                leading = max(prev.class_probs.items(), key=lambda kv: kv[1])[0] if prev.class_probs else None
                mob_class = MOBILITY_CLASS_BY_PLATFORM.get(leading or "", "ground_fast")
                exi_decay, unc_growth = MOBILITY_DECAY[mob_class]
                new_existence = max(0.0, prev.existence - exi_decay * turns_since)
                new_unc = prev.position_uncertainty_hexes + unc_growth * turns_since
                if new_existence < 0.04:
                    continue   # too stale to render
                contacts.append(Contact(
                    contact_id=prev.contact_id,
                    target_kind=prev.target_kind,
                    believed_col=prev.believed_col,
                    believed_row=prev.believed_row,
                    position_uncertainty_hexes=new_unc,
                    existence=new_existence,
                    class_probs=prev.class_probs,
                    last_refined_turn=prev.last_refined_turn,
                    currently_observed=False,
                    contributing_sensor_ids=[],
                    dominant_modality=prev.dominant_modality,
                ))

        new_contacts[observing_side] = contacts

    state.contacts = new_contacts
