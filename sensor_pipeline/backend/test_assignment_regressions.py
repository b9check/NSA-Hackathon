import math
import random
import unittest

from fusion import FusionEngine, compute_existence
from sensors import (
    ALL_CLASSES,
    Classification,
    EmissionType,
    Observation,
    Position,
    RadarSensor,
    SensorType,
    Target,
)
from server import create_scenario


class AssignmentRegressionTests(unittest.TestCase):
    def test_detection_probability_has_hard_range_cutoff(self):
        radar = RadarSensor("RADAR-TEST", Position(0, 0), max_range_km=150)
        target = Target(Position(155, 0), Classification.VEHICLE_CONVOY)

        self.assertEqual(radar._detection_probability(151), 0.0)
        self.assertIsNone(radar.observe(target, 0.0))

    def test_incompatible_sigint_emissions_do_not_form_track(self):
        engine = FusionEngine()
        obs = [
            Observation(
                sensor_type=SensorType.SIGINT,
                sensor_id="SIGINT-A",
                sensor_position=Position(0, 0),
                sensor_max_range_km=200,
                timestamp=0.0,
                bearing=45.0,
                bearing_error=4.0,
                emission_type=EmissionType.FIRE_CONTROL,
                detection_probability=0.95,
            ),
            Observation(
                sensor_type=SensorType.SIGINT,
                sensor_id="SIGINT-B",
                sensor_position=Position(100, 0),
                sensor_max_range_km=200,
                timestamp=0.0,
                bearing=315.0,
                bearing_error=4.0,
                emission_type=EmissionType.COMMS_VHF,
                detection_probability=0.95,
            ),
        ]

        engine.process_observations(obs, 0.0)
        self.assertEqual(engine.tracks, [])

    def test_demo_scenario_does_not_create_high_confidence_assignment_ghosts(self):
        random.seed(42)
        sensors, targets = create_scenario()
        engine = FusionEngine()
        truth = {
            t.target_id: (t.position.x, t.position.y, t.classification.value)
            for t in targets
        }

        for cycle in range(12):
            observations = []
            for sensor in sensors:
                for target in targets:
                    obs = sensor.observe(target, float(cycle))
                    if obs:
                        obs.truth_id = target.target_id
                        observations.append(obs)

            clusters = engine._cluster(observations, float(cycle))
            for obs_list in clusters["existing"].values():
                truth_ids = {o.truth_id for o in obs_list}
                self.assertLessEqual(
                    len(truth_ids),
                    1,
                    f"mixed existing assignment at cycle {cycle + 1}: {truth_ids}",
                )

            for obs_list in clusters["new"]:
                truth_ids = {o.truth_id for o in obs_list}
                if len(truth_ids) > 1:
                    self.assertLess(
                        compute_existence(obs_list),
                        engine.new_track_min_existence,
                        f"mixed new cluster strong enough to create track at cycle {cycle + 1}: {truth_ids}",
                    )

            engine.process_observations(observations, float(cycle))
            active_tracks = [tr for tr in engine.tracks if tr.currently_observed]
            self.assertLessEqual(len(active_tracks), len(targets))

            for tr in active_tracks:
                nearest = min(
                    truth.items(),
                    key=lambda kv: math.hypot(
                        tr.position.x - kv[1][0],
                        tr.position.y - kv[1][1],
                    ),
                )
                err = math.hypot(
                    tr.position.x - nearest[1][0],
                    tr.position.y - nearest[1][1],
                )
                self.assertFalse(
                    err > 12.0 and tr.existence > 0.25,
                    f"high-confidence ghost {tr.track_id} at cycle {cycle + 1}: "
                    f"nearest={nearest[0]}, err={err:.1f}, existence={tr.existence:.2f}",
                )

    def test_fixed_site_persists_with_slow_uncertainty_growth_when_unseen(self):
        radar = RadarSensor("RADAR-TEST", Position(0, 0), max_range_km=150)
        target = Target(
            Position(50, 70),
            Classification.SAM_BATTERY,
            EmissionType.FIRE_CONTROL,
            is_emitting=True,
        )
        engine = FusionEngine()
        random.seed(7)
        obs = radar.observe(target, 0.0)
        self.assertIsNotNone(obs)
        engine.process_observations([obs], 0.0)
        self.assertEqual(len(engine.tracks), 1)

        tr = engine.tracks[0]
        tr.class_probs_conditional = {
            c: (1.0 if c == Classification.SAM_BATTERY else 0.0)
            for c in ALL_CLASSES
        }
        tr.track_confidence = 0.90
        tr.existence = 0.90
        for cycle in range(1, 11):
            engine.process_observations([], float(cycle))

        self.assertEqual(len(engine.tracks), 1)
        tr = engine.tracks[0]
        major, minor, _ = tr.ellipse_params()
        self.assertFalse(tr.currently_observed)
        self.assertGreater(tr.track_confidence, 0.75)
        self.assertLess(major, 4.0)
        self.assertLess(minor, 4.0)
        self.assertAlmostEqual(tr.position.x, tr.state_vector[0])
        self.assertAlmostEqual(tr.position.y, tr.state_vector[1])


if __name__ == "__main__":
    unittest.main()
