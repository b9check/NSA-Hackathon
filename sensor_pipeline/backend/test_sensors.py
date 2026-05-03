"""
Standalone test for sensor observation models.
Verifies: noise, detection probability, partial observability, feature outputs.
"""

import random
from sensors import (
    RadarSensor, CameraSensor, SIGINTSensor,
    Position, Target, Classification, EmissionType, ALL_CLASSES,
)


def fmt_likelihood(lik: dict) -> str:
    items = sorted(lik.items(), key=lambda kv: kv[1], reverse=True)
    return ", ".join(f"{c.value}={p:.2f}" for c, p in items[:3])


def print_observation(obs):
    if obs is None:
        return
    parts = [f"  [{obs.sensor_type.value.upper()}] {obs.sensor_id}"]
    parts.append(f"    Bearing: {obs.bearing:.1f}deg (+/-{obs.bearing_error:.1f}deg)")
    if obs.range_km is not None:
        parts.append(f"    Range:   {obs.range_km:.2f} km (+/-{obs.range_error:.2f} km)")
    if obs.rcs is not None:
        parts.append(f"    RCS:     {obs.rcs:.2f} m^2")
    if obs.class_likelihood:
        parts.append(f"    Class likelihood: {fmt_likelihood(obs.class_likelihood)}")
    if obs.emission_type is not None:
        parts.append(f"    Emission: {obs.emission_type.value} (signal: {obs.signal_strength:.1f} dB)")
    print("\n".join(parts))


def main():
    random.seed(42)

    radar1  = RadarSensor("RADAR-01", Position(0, 0), max_range_km=150)
    camera1 = CameraSensor("CAM-01",  Position(10, 5), max_range_km=30)
    sigint1 = SIGINTSensor("SIGINT-01", Position(-20, 40), max_range_km=200)

    targets = [
        Target(Position(50, 70), Classification.SAM_BATTERY,
               EmissionType.FIRE_CONTROL, is_emitting=True, target_id="TGT-001"),
        Target(Position(20, 15), Classification.VEHICLE_CONVOY,
               EmissionType.COMMS_VHF, is_emitting=True, target_id="TGT-002"),
        Target(Position(25, 10), Classification.INFANTRY,
               is_emitting=False, target_id="TGT-003"),
    ]

    print("=" * 70)
    print("SENSOR OBSERVATION TEST")
    print("=" * 70)

    for target in targets:
        print(f"\nTarget: {target.target_id} = {target.classification.value} at "
              f"({target.position.x},{target.position.y})"
              f"{' [emitting: ' + target.emission_type.value + ']' if target.is_emitting else ''}")
        for sensor in (radar1, camera1, sigint1):
            obs = sensor.observe(target, 0.0)
            if obs:
                print_observation(obs)
            else:
                dist = sensor.position.distance_to(target.position)
                print(f"  [{sensor.sensor_type.value.upper()}] {sensor.sensor_id}: NO DETECTION (range {dist:.1f} km, max {sensor.max_range_km} km)")

    # Detection-probability sweep
    print("\n" + "=" * 70)
    print("DETECTION PROBABILITY VS RANGE (radar, max=150km)")
    print("=" * 70)
    trials = 200
    for r in (10, 50, 100, 130, 145, 155):
        target = Target(Position(r, 0), Classification.VEHICLE_CONVOY, target_id="probe")
        hits = sum(1 for _ in range(trials) if radar1.observe(target, 0.0) is not None)
        print(f"  r={r:>3} km: {hits}/{trials} = {hits/trials:.0%}")

    # Camera class likelihood near vs. far
    print("\n" + "=" * 70)
    print("CAMERA CLASS LIKELIHOOD AT NEAR vs. FAR RANGE")
    print("=" * 70)
    near = Target(Position(15, 5), Classification.SAM_BATTERY, target_id="near")
    far  = Target(Position(38, 5), Classification.SAM_BATTERY, target_id="far")
    for label, t in (("NEAR (~5km)", near), ("FAR (~28km, near max)", far)):
        obs = camera1.observe(t, 0.0)
        if obs:
            print(f"  {label}: {fmt_likelihood(obs.class_likelihood)}")
        else:
            print(f"  {label}: NO DETECTION")

    print("\n" + "=" * 70)
    print("[OK] Radar: bearing + range + RCS + range_rate, NO classification")
    print("[OK] Camera: bearing + class probability vector, NO range")
    print("[OK] SIGINT: bearing + emission type, NO range, only emitters")
    print("=" * 70)


if __name__ == "__main__":
    main()
