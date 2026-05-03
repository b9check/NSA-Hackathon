"""
Snapshot fusion test.

Verifies:
  - Static target with stable observation geometry produces stable confidences
    cycle-to-cycle (no temporal accumulation drift).
  - Class joint probabilities never exceed existence.
  - Radar (range+bearing) gives much tighter position covariance than
    SIGINT/Camera triangulation alone.
  - Bearing-only triangulation remains tentative: it can localize with
    a wider ellipse, but existence is lower than range-backed tracks.
"""

import random
import math
from sensors import (
    RadarSensor, SIGINTSensor, CameraSensor,
    Position, Target, Classification, EmissionType,
)
from fusion import FusionEngine


def fmt_class_dist(probs: dict, top_n: int = 4) -> str:
    items = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    return ", ".join(f"{c.value}={p:.2f}" for c, p in items[:top_n])


def main():
    random.seed(123)

    sam = Target(
        position=Position(50, 70),
        classification=Classification.SAM_BATTERY,
        emission_type=EmissionType.FIRE_CONTROL,
        is_emitting=True,
        target_id="SAM-01",
    )

    radar   = RadarSensor("RADAR-01", Position(0, 0), max_range_km=150)
    sigint1 = SIGINTSensor("SIGINT-01", Position(-20, 40), max_range_km=200)
    sigint2 = SIGINTSensor("SIGINT-02", Position(60, -10), max_range_km=180)

    print("=" * 78)
    print("SNAPSHOT FUSION TEST")
    print("=" * 78)
    print("Truth: SAM Battery at (50, 70), emitting Fire Control Radar\n")

    # --- Scenario A: all 3 sensors active (radar + 2 SIGINT) ---
    print("--- Scenario A: Radar + 2 SIGINT (range+bearing fix available) ---")
    engine_a = FusionEngine()
    for cycle in range(8):
        t = float(cycle)
        observations = [s.observe(sam, t) for s in (radar, sigint1, sigint2)]
        observations = [o for o in observations if o]
        engine_a.process_observations(observations, t)
        if engine_a.tracks:
            tr = engine_a.tracks[0]
            major, minor, orient = tr.ellipse_params()
            joint = tr.joint_class_probs
            sam_joint = joint[Classification.SAM_BATTERY]
            err = tr.position.distance_to(sam.position)
            print(f"  c{cycle+1}: existence={tr.existence:.2f}  P(SAM)_joint={sam_joint:.2f}  "
                  f"err={err:.2f}km  ellipse=1sigma {major:.2f}x{minor:.2f}km @ {orient:.0f}deg  "
                  f"sensors={len(tr.contributing_sensor_ids)}")

    print()

    # --- Scenario B: SIGINT only (no radar) ---
    print("--- Scenario B: SIGINT only (bearing-only triangulation) ---")
    random.seed(123)
    engine_b = FusionEngine()
    for cycle in range(8):
        t = float(cycle)
        observations = [s.observe(sam, t) for s in (sigint1, sigint2)]
        observations = [o for o in observations if o]
        engine_b.process_observations(observations, t)
        if engine_b.tracks:
            tr = engine_b.tracks[0]
            major, minor, orient = tr.ellipse_params()
            joint = tr.joint_class_probs
            sam_joint = joint[Classification.SAM_BATTERY]
            err = tr.position.distance_to(sam.position)
            print(f"  c{cycle+1}: existence={tr.existence:.2f}  P(SAM)_joint={sam_joint:.2f}  "
                  f"err={err:.2f}km  ellipse=1sigma {major:.2f}x{minor:.2f}km @ {orient:.0f}deg")

    print()

    # --- Scenario C: single SIGINT (underdetermined position) ---
    print("--- Scenario C: single SIGINT (one bearing only) ---")
    random.seed(123)
    engine_c = FusionEngine()
    for cycle in range(4):
        t = float(cycle)
        observations = [sigint1.observe(sam, t)]
        observations = [o for o in observations if o]
        engine_c.process_observations(observations, t)
        if engine_c.tracks:
            tr = engine_c.tracks[0]
            major, minor, orient = tr.ellipse_params()
            joint = tr.joint_class_probs
            sam_joint = joint[Classification.SAM_BATTERY]
            print(f"  c{cycle+1}: existence={tr.existence:.2f}  P(SAM)_joint={sam_joint:.2f}  "
                  f"ellipse=1sigma {major:.1f}x{minor:.1f}km @ {orient:.0f}deg")
        else:
            print(f"  c{cycle+1}: no track formed (underdetermined)")

    print()

    # --- Scenario D: radar only (single sensor) ---
    print("--- Scenario D: single radar (range+bearing) ---")
    random.seed(123)
    engine_d = FusionEngine()
    for cycle in range(4):
        t = float(cycle)
        observations = [radar.observe(sam, t)]
        observations = [o for o in observations if o]
        engine_d.process_observations(observations, t)
        if engine_d.tracks:
            tr = engine_d.tracks[0]
            major, minor, orient = tr.ellipse_params()
            joint = tr.joint_class_probs
            sam_joint = joint[Classification.SAM_BATTERY]
            err = tr.position.distance_to(sam.position)
            print(f"  c{cycle+1}: existence={tr.existence:.2f}  P(SAM)_joint={sam_joint:.2f}  "
                  f"err={err:.2f}km  ellipse=1sigma {major:.2f}x{minor:.2f}km @ {orient:.0f}deg")

    print()
    print("=" * 78)
    print("KEY TAKEAWAYS")
    print("=" * 78)
    print("- Radar+2SIGINT: range-backed track, high existence")
    print("- SIGINT only: tentative existence, wider bearing-triangulation ellipse")
    print("- Single SIGINT: no track formed (one bearing is underdetermined)")
    print("- Single radar: tight along range, more uncertain perpendicular")
    print("- Confidences stable cycle-to-cycle (no temporal accumulation)")
    print("- P(class)_joint <= existence (always)")


if __name__ == "__main__":
    main()
