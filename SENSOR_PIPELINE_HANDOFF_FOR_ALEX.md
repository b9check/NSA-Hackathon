# Sensor Pipeline Handoff for Alex

This document is a detailed handoff for merging the current `sensor_pipeline`
work into the larger project. It describes the code structure, runtime flow,
sensor models, fusion logic, frontend contract, test coverage, and merge risks.

The short version: `sensor_pipeline` is a self-contained FastAPI + React demo
that simulates radar, EO/IR camera, and SIGINT observations, fuses them into
persistent tracks, and streams a tactical display over WebSocket. The current
fusion model is not just a per-cycle snapshot. Tracks have a persistent
Kalman-style state, mobility-aware confidence decay, range-aware association,
per-sensor ID evidence smoothing, and a UI that separates track confidence,
position uncertainty, current sensor tracking, tactical category, and class
estimate.

## Directory Layout

```text
sensor_pipeline/
  __init__.py
  run.bat
  run.sh

  backend/
    __init__.py
    requirements.txt
    sensors.py
    fusion.py
    server.py
    test_sensors.py
    test_fusion.py
    test_assignment_regressions.py
    server_stdout.log          generated runtime log
    server_stderr.log          generated runtime log
    __pycache__/               generated Python cache

  frontend/
    package.json
    package-lock.json
    public/index.html
    src/index.js
    src/App.js
    build/                     generated production bundle
    node_modules/              generated local dependencies
```

Recommended merge handling:

- Commit source files, tests, lockfiles, scripts, and this handoff.
- Usually do not commit `frontend/node_modules/`, `backend/__pycache__/`, or
  `backend/server_*.log`.
- Whether to commit `frontend/build/` depends on deployment. The FastAPI server
  serves `frontend/build` directly if it exists. For a demo branch, committing
  the build can be convenient. For a normal source branch, regenerate it with
  `npm.cmd run build` or `npm run build`.

## How to Run

Windows:

```powershell
cd sensor_pipeline
.\run.bat
```

macOS/Linux:

```bash
cd sensor_pipeline
./run.sh
```

Manual backend:

```powershell
cd sensor_pipeline\backend
python server.py
```

Manual frontend build:

```powershell
cd sensor_pipeline\frontend
npm.cmd install
npm.cmd run build
```

The running app serves:

- Tactical display: `http://localhost:8000`
- Static scenario API: `http://localhost:8000/api/scenario`
- Reset API: `POST http://localhost:8000/api/reset`
- Stream: `ws://localhost:8000/ws`

## Runtime Data Flow

The full demo loop is:

1. `server.py` creates a deterministic scenario with sensors and targets.
2. Every WebSocket cycle, each sensor attempts to observe each target.
3. `sensors.py` returns zero or one `Observation` per sensor-target pair.
4. `fusion.py` predicts existing tracks forward one cycle.
5. `fusion.py` associates fresh observations to predicted tracks.
6. Unmatched observations are clustered into possible new tracks.
7. Matched tracks get Kalman/EKF measurement updates.
8. Unmatched tracks stay alive, but confidence decays by mobility category and
   covariance grows.
9. Each track is serialized to JSON by `Track.to_dict()`.
10. `server.py` sends the state over WebSocket.
11. `frontend/src/App.js` redraws the canvas display and inspect panel.

The frontend does not run any fusion logic. It consumes the belief state that
the backend emits.

## Coordinate and Geometry Conventions

Positions are in kilometers:

- `x`: east
- `y`: north

Bearings are in degrees:

- `0 deg`: north
- `90 deg`: east
- clockwise positive

There is intentionally no field-of-view gating. All sensors are effectively
360-degree sensors. Detection depends on range, emission state for SIGINT, and
random detection probability.

## Backend: `sensors.py`

`sensors.py` defines the simulated world primitives and sensor observation
models.

### Enums

`SensorType`:

- `radar`
- `camera`
- `sigint`

`Classification`:

- `SAM Battery`
- `Radar Station`
- `Command Post`
- `Vehicle Convoy`
- `Infantry`
- `Aircraft`

`EmissionType`:

- `Search Radar`
- `Fire Control Radar`
- `HF Communications`
- `VHF Communications`
- `Jamming`
- `Data Link`

`ALL_CLASSES` is the stable ordered list used everywhere the classifier needs
to iterate over all classes.

### Position

`Position` is a small dataclass:

```python
Position(x: float, y: float)
```

Important methods:

- `distance_to(other)`: Euclidean distance in km.
- `bearing_to(other)`: bearing from this position to `other`, using the
  0-deg-north, clockwise-positive convention.

### Target

`Target` represents simulated truth:

```python
Target(
    position: Position,
    classification: Classification,
    emission_type: Optional[EmissionType] = None,
    is_emitting: bool = False,
    target_id: str = "",
    true_rcs: Optional[float] = None,
)
```

Important behavior:

- `true_rcs` is persistent.
- `get_or_init_rcs()` samples true RCS once from the class distribution.
- Later radar observations add measurement noise around that fixed RCS.

This matters because earlier versions effectively re-rolled target RCS on every
observation, which made class inference unstable and unrealistic.

### RCS Profiles

`RCS_PROFILES` stores log-normal class distributions:

```python
Classification -> (mean log RCS, std dev log RCS)
```

Current approximate intent:

- SAM Battery: around 20 m2
- Radar Station: around 100 m2
- Command Post: around 40 m2
- Vehicle Convoy: around 8 m2
- Infantry: around 0.5 m2
- Aircraft: around 15 m2 with high variance

Radar never directly outputs a class label. It outputs RCS, and fusion converts
that RCS into class likelihoods.

### Observation

`Observation` is the shared sensor report object:

```python
Observation(
    sensor_type,
    sensor_id,
    sensor_position,
    sensor_max_range_km,
    timestamp,
    detected=True,
    bearing=None,
    bearing_error=None,
    range_km=None,
    range_error=None,
    rcs=None,
    range_rate=None,
    class_likelihood=None,
    emission_type=None,
    signal_strength=None,
    detection_probability=None,
    association_confidence=1.0,
)
```

Important fields:

- `sensor_max_range_km`: used by fusion to prevent a bearing-only observation
  from attaching to a track outside that sensor's range.
- `detection_probability`: the range-dependent probability the sensor had at
  that target distance. Fusion uses it to weight track confidence.
- `association_confidence`: set later by fusion. Clean matches are near `1.0`.
  Weak bearing-only coincidences are lower.

### Detection Probability Model

All sensors inherit `_detection_probability(distance_km)` from `SensorBase`.

The model is logistic with a hard cutoff:

```text
if distance > max_range:
    P_detect = 0
else:
    P_detect = max_detection_probability /
               (1 + exp(detection_steepness * (range_ratio - detection_knee)))
```

where:

```text
range_ratio = distance / max_range
```

Current sensor-specific values:

| Sensor | Max P(detect) | Knee | Steepness | Meaning |
| --- | ---: | ---: | ---: | --- |
| Radar | 0.98 | 0.86 | 12.0 | Very reliable until near max range |
| Camera | 0.78 | 0.55 | 11.0 | Less reliable and falls off earlier |
| SIGINT | 0.80 | 0.68 | 8.0 | Moderate reliability, smoother falloff |

The hard cutoff is important. A sensor can never produce an observation beyond
its `max_range_km`.

### RadarSensor

Radar output:

- bearing
- bearing error
- range
- range error
- RCS
- range rate, currently `0.0`
- detection probability

Radar does not output visual classification. It contributes to classification
only through RCS likelihood.

Current radar noise:

- Bearing sigma: `1.5 deg`
- Range sigma: `max(0.05 km, 1% of distance)`
- RCS measurement noise: log-normal sigma `0.4`

### CameraSensor

Camera output:

- bearing
- bearing error
- class likelihood vector
- detection probability

Camera does not output range.

The class likelihood vector is sharper at close range and flatter near max
range. The code uses a softmax over noisy logits with confusion pairs:

- SAM Battery <-> Radar Station
- Vehicle Convoy <-> Command Post
- Infantry <-> Vehicle Convoy

Camera is the only sensor that directly emits a visual class-likelihood vector.

### SIGINTSensor

SIGINT output:

- bearing
- bearing error
- emission type
- signal strength
- detection probability

SIGINT only observes targets where:

```python
target.is_emitting is True
target.emission_type is not None
```

SIGINT does not output range.

Current SIGINT bearing sigma: `4.0 deg`.

## Backend: `fusion.py`

`fusion.py` is the core of the system. It turns noisy partial observations into
persistent tracks.

### Important Naming

The current operator-facing concept is `track_confidence`.

There is still a field named `existence` in the API and code. Treat it as a
backward-compatible alias for `track_confidence`. Older comments and tests may
still say "existence" or "snapshot"; the actual current behavior is persistent
track confidence with prediction and decay.

### Knowledge Bases

#### Emitter Library

`EMITTER_LIBRARY` maps each `EmissionType` to a likelihood over classes.

Examples:

- `FIRE_CONTROL` strongly favors `SAM Battery`, then `Radar Station`.
- `SEARCH_RADAR` strongly favors `Radar Station`, then `SAM Battery`.
- `COMMS_HF` favors `Command Post`.
- `COMMS_VHF` favors `Vehicle Convoy`, then `Command Post`.
- `JAMMING` and `DATA_LINK` favor `Aircraft` but can also support other classes.

This is why a track can be classified even if no camera sees it. The class is
then a signature-based estimate, not visual ID.

#### Tactical Categories

The old air/land/sea "type" concept was replaced with tactical category:

| Class | Category |
| --- | --- |
| Aircraft | Air |
| SAM Battery | Fixed Site |
| Radar Station | Fixed Site |
| Command Post | C2 |
| Vehicle Convoy | Mobile Ground |
| Infantry | Mobile Ground |

Category is derived from the conditional class distribution. It is not a
separate sensor measurement. It matters because it drives mobility and
persistence.

#### Mobility Model

`MOBILITY_MODEL_BY_CATEGORY` controls covariance growth and confidence decay:

| Category | Position Q km2/cycle | Velocity Q km2/cycle | Confidence decay |
| --- | ---: | ---: | ---: |
| Fixed Site | 0.50 | 0.01 | 0.015 |
| C2 | 1.00 | 0.03 | 0.020 |
| Mobile Ground | 5.00 | 0.15 | 0.060 |
| Air | 200.00 | 5.00 | 0.180 |

If the class distribution is uncertain, fusion computes a probability-weighted
mixture of the category models.

This gives the desired behavior:

- A SAM battery last seen several cycles ago remains a strong track at roughly
  the same location, with slowly growing uncertainty.
- A convoy last seen several cycles ago remains plausible but gets a larger
  uncertainty region and faster confidence decay.
- An aircraft becomes uncertain and decays quickly when not detected.

#### Sensor Quality

`SENSOR_QUALITY` is the base confidence contribution by sensor type:

| Sensor | Base quality |
| --- | ---: |
| Radar | 0.70 |
| Camera | 0.55 |
| SIGINT | 0.55 |

This is not the same as detection probability. Detection probability depends on
range and random trial outcome. Sensor quality is the base reliability used
after a detection exists.

### Track Object

`Track` is the persistent fused object.

Key fields:

```python
track_id: str
position: Position
position_cov: list          # 2x2 covariance for x/y
state_vector: list          # [x, y, vx, vy]
state_cov: list             # 4x4 covariance
last_observed_time: float

sensor_evidence: dict       # per-sensor class evidence with timestamps
rcs_estimate: Optional[float]
rcs_samples: int
emission_type_counts: dict
emission_evidence: dict

currently_observed: bool
existence: float            # backward-compatible alias
track_confidence: float
cycles_since_last_seen: float
class_probs_conditional: dict
contributing_sensor_ids: list
last_observations: list
```

Key derived properties:

- `joint_class_probs`: `track_confidence * P(class | track is real)`
- `category_probs_conditional`: tactical category distribution
- `type_probs`: currently an alias for joint tactical category probabilities
- `leading_category`: max category by conditional probability
- `ellipse_params()`: 1-sigma major/minor/orientation from `position_cov`

### Track JSON Contract

`Track.to_dict()` is what the frontend and any strategy layer should consume.

Important fields:

```json
{
  "track_id": "TRK-0001",
  "position": {"x": 50.1, "y": 70.0},
  "velocity": {"vx": 0.0, "vy": 0.0},
  "position_uncertainty": {
    "ellipse_major_km": 1.2,
    "ellipse_minor_km": 0.7,
    "orientation_deg": 34.0
  },
  "rcs_estimate": 22.0,
  "currently_observed": true,
  "currently_detected": true,
  "last_observed_time": 8.0,
  "cycles_since_last_seen": 0.0,
  "track_confidence": 0.91,
  "existence": 0.91,
  "class_joint_probs": {
    "SAM Battery": 0.74
  },
  "class_probs_conditional": {
    "SAM Battery": 0.81
  },
  "category_joint_probs": {
    "Fixed Site": 0.84
  },
  "category_probs_conditional": {
    "Fixed Site": 0.92
  },
  "leading_class": "SAM Battery",
  "leading_class_prob": 0.74,
  "leading_category": "Fixed Site",
  "leading_category_prob": 0.84,
  "dominant_emission_type": "Fire Control Radar",
  "contributing_sensor_ids": ["RADAR-01", "SIGINT-01"],
  "last_observations": []
}
```

Important interpretation:

- `track_confidence`: belief that this track is still a relevant real asset.
- `currently_detected`: whether any sensor contributed to this track this cycle.
- `contributing_sensor_ids`: sensors currently tracking it this cycle only.
- `cycles_since_last_seen`: current cycle minus last observation cycle.
- `class_probs_conditional`: what we think the class is, assuming the track is
  real.
- `class_joint_probs`: class probability multiplied by track confidence.
- `leading_class_prob`: currently joint, not conditional.
- `category_probs_conditional`: tactical/mobility category, assuming real.

For agents and strategy code, prefer:

- `track_confidence`
- `currently_detected`
- `cycles_since_last_seen`
- `position`
- `velocity`
- `position_uncertainty`
- `class_probs_conditional`
- `category_probs_conditional`
- `contributing_sensor_ids`
- `last_observations`

Avoid building game logic around the legacy names `existence`, `leading_type`,
or `type_joint_probs` unless backward compatibility is needed.

### Position Snapshot Helper

`compute_position_snapshot(observations, init_guess)` is used to initialize new
tracks and to evaluate new clusters.

It supports:

- range+bearing observations
- bearing-only observations if there are at least two
- mixed range+bearing plus bearing-only observations

Range+bearing observations produce direct measured positions using:

```text
x = sensor_x + range * sin(bearing)
y = sensor_y + range * cos(bearing)
```

The range/bearing measurement covariance is built from:

- radial range variance
- tangential bearing variance

Bearing-only observations are linearized around the current estimate and folded
into a Fisher information matrix. The solver iterates a fixed-point update up
to 8 times.

One bearing-only observation cannot create a position estimate by itself.

### Track Confidence Calculation

`compute_existence(observations)` computes the current-cycle confidence support
from observations. Despite the function name, conceptually this is cycle-level
track support.

Formula:

```text
track_support = 1 - product(1 - q_effective_i)
```

over unique sensor IDs.

For each sensor:

```text
q_effective =
    SENSOR_QUALITY[sensor_type]
    * min(1, detection_probability / 0.95)
    * association_confidence
    * localization_factor
```

`localization_factor`:

- `1.0` if at least one observation in the cluster has a range fix.
- `0.45` if the whole cluster is bearing-only.

Consequences:

- Radar near its reliable range contributes close to its base quality.
- Marginal long-range detections contribute much less.
- Weak bearing-only intersections remain tentative.
- Multiple independent sensors combine multiplicatively.
- Multiple observations from the same sensor do not double-count.

### Classification Logic

Classification is Bayesian evidence fusion over per-sensor likelihoods.

Input evidence can come from:

- Camera class-likelihood vector.
- SIGINT emission likelihood via `EMITTER_LIBRARY`.
- Radar RCS likelihood via `rcs_likelihood`.

This means a camera is not required for classification. Without a camera, the
classification is a signature-based estimate from RCS and/or emissions.

`_observation_class_likelihood()` builds a likelihood for one observation:

- Add camera vector if present.
- Add emission likelihood if present.
- Add RCS likelihood if present.
- Multiply the likelihood vectors elementwise.
- Normalize.
- Apply a small floor so one bad sensor cannot permanently zero out a class.

Per-sensor evidence is smoothed with a geometric mean:

```text
log_smoothed =
    (1 - alpha) * log(old_likelihood)
    + alpha * log(new_likelihood)
```

with `id_smoothing = 0.3`.

Evidence stale behavior:

- Each per-sensor evidence record has `last_refresh`.
- Evidence older than `evidence_stale_after_cycles = 5` is dropped.
- This prevents one misassigned sensor from poisoning ID forever.

This design keeps the useful snapshot insight: ID confidence should not climb
forever just because the same sensor repeats the same observation.

### EKF / Persistence Logic

Each track uses state:

```text
[x, y, vx, vy]
```

and a 4x4 covariance.

Each cycle:

1. Predict all existing tracks forward.
2. Associate observations to predicted positions.
3. Update matched tracks.
4. Decay unmatched tracks.
5. Drop tracks below thresholds.

#### Predict Step

`_predict_track(tr, dt)`:

- Ensures a state vector/covariance exists.
- Computes mobility parameters from the current class distribution.
- Computes a `motion_factor` from category probabilities.
- Builds a transition matrix `F`.
- Builds process noise `Q`.
- Updates:

```text
state = F * state
cov = F * cov * F^T + Q
```

Fixed sites keep low velocity influence. Mobile and air tracks allow more
motion and uncertainty growth.

#### Measurement Updates

Range+bearing observations:

- Converted to a 2D position measurement.
- Updated with a standard linear Kalman position update.
- Measurement covariance comes from range/bearing noise.

Bearing-only observations:

- Updated with a 1D EKF bearing residual.
- Bearing residual is angularly normalized.
- The measurement Jacobian is with respect to `[x, y]`.

#### Missed Detections

If a track gets no observations in a cycle:

```python
track_confidence *= exp(-decay_rate * dt)
```

where `decay_rate` is the category-mixture decay rate.

The track remains in the output until:

- `cycles_since_seen > stale_after_cycles`, or
- `track_confidence < drop_confidence_threshold`

Current values:

```python
stale_after_cycles = 30
drop_confidence_threshold = 0.08
```

Current-cycle sensor fields are cleared when unseen:

- `currently_observed = False`
- `contributing_sensor_ids = []`
- `last_observations = []`

Position remains predicted and covariance grows.

### Association and Clustering

The association pipeline is in `_cluster()`.

High-level sequence:

1. Split detected observations into range+bearing and bearing-only.
2. Assign range+bearing observations to existing tracks first.
3. Assign bearing-only observations to existing tracks second.
4. Put unassigned observations into new-track clusters.

#### Cannot-Link Rule

Within any existing track update or new cluster, there can be at most one
observation from a given sensor in the same cycle.

Reason: if one sensor reports two separate detections, those detections are
different objects, not two measurements of the same object.

This prevents close objects like convoy and infantry from being collapsed into
one track when a sensor sees both.

#### Range Feasibility for Bearing-Only Observations

Bearing-only observations still carry `sensor_max_range_km`.

Before a bearing-only observation can attach to a track or cluster, fusion
checks:

```text
distance(sensor_position, candidate_position)
    <= sensor_max_range_km * (1 + range_margin_pct)
```

Current margin:

```python
range_margin_pct = 0.10
```

This prevents a camera or SIGINT bearing from attaching to a track far outside
that sensor's possible detection range.

#### Existing Track Scores

Range+bearing to track:

- Convert observation to 2D measured position.
- Compare to predicted track position using Mahalanobis distance.
- Gate with `position_gate_chi2 = 16.0`.
- Association confidence is `max(0.20, exp(-0.125 * chi2))`.

Bearing-only to track:

- Check emission compatibility.
- Check range feasibility.
- Compare predicted bearing to measured bearing.
- Gate with `bearing_gate_sigma = 2.0`.
- Apply emission compatibility adjustment.
- Reject if association confidence is below `min_bearing_association = 0.25`.

#### New Track Clustering

Unmatched observations are clustered:

1. Range+bearing observations form seeds.
2. Range+bearing seeds can merge if statistically compatible and no same-sensor
   conflict exists.
3. Bearing-only observations attach to range-backed seeds if bearing and range
   feasibility allow it.
4. If no range-backed seed exists, two or more bearing-only observations can
   form a bearing-only triangulation cluster.
5. Single bearing-only observations do not create tracks because position is
   underdetermined.

New track threshold:

```python
new_track_min_existence = 0.15
```

### Track Creation and Refresh

`_create_track()`:

- Computes initial position/covariance from the observation cluster.
- Rejects if no estimable position.
- Rejects if cycle support is below `new_track_min_existence`.
- Initializes `[x, y, 0, 0]`.
- Initializes 4x4 covariance from 2D position covariance.
- Calls `_refresh_track(..., update_kinematics=False)`.

The `update_kinematics=False` detail is important. The initial position already
came from the same observations. Updating the Kalman state again with those same
observations would double-count the first measurement and make covariance too
optimistic.

`_refresh_track()`:

- Optionally applies EKF/Kalman measurement update.
- Smooths RCS estimate in log space.
- Updates per-sensor class evidence.
- Drops stale evidence.
- Recomputes conditional class posterior.
- Updates track confidence:

```python
track_confidence = min(0.99, max(track_confidence * 0.98, cycle_confidence))
```

This keeps confidence from ratcheting upward forever, but avoids dropping a
strong track just because one cycle was weaker.

## Backend: `server.py`

`server.py` owns the FastAPI app, demo scenario, simulation loop, static API,
WebSocket stream, and frontend static serving.

### Scenario

Sensors:

| ID | Type | Position | Range |
| --- | --- | --- | ---: |
| RADAR-01 | Radar | `(0, 0)` | 150 km |
| RADAR-02 | Radar | `(80, 20)` | 120 km |
| CAM-01 | Camera | `(10, 5)` | 30 km |
| CAM-02 | Camera | `(55, 45)` | 35 km |
| SIGINT-01 | SIGINT | `(-20, 40)` | 200 km |
| SIGINT-02 | SIGINT | `(60, -10)` | 180 km |

Targets:

| ID | Class | Position | Emission |
| --- | --- | --- | --- |
| SAM-01 | SAM Battery | `(50, 70)` | Fire Control Radar |
| CONVOY-01 | Vehicle Convoy | `(20, 15)` | VHF Communications |
| RADAR-SITE-01 | Radar Station | `(100, 60)` | Search Radar |
| INF-01 | Infantry | `(25, 10)` | none |
| CP-01 | Command Post | `(70, 85)` | HF Communications |
| SAM-02 | SAM Battery | `(130, 40)` | Fire Control Radar |

Each target has persistent RCS initialized at scenario creation.

### SimulationState

`SimulationState.step()`:

1. Uses current `cycle` as timestamp.
2. Loops over every sensor-target pair.
3. Calls `sensor.observe(target, t)`.
4. Sends all observations to `FusionEngine.process_observations()`.
5. Increments cycle.
6. Returns JSON-serializable state.

`SimulationState.reset()` resets:

- fusion engine
- cycle counter

It does not recreate sensors/targets. Persistent target RCS remains stable
across reset in the same server process.

### WebSocket Behavior

On WebSocket connect:

1. Server sends a `"scenario"` message containing sensor metadata.
2. Server resets fusion.
3. Server seeds Python random with `42`.
4. Server loops:
   - steps simulation
   - sends `"update"`
   - waits up to 1.5 seconds for client commands

Supported client commands:

- `{"command": "reset"}`
- `{"command": "step"}` currently does nothing special because the loop already
  steps continuously.

### Static API

`GET /api/scenario` returns sensors and truth targets.

Important: the API includes truth target positions/classes. This is fine for a
demo visualizer, but should not be exposed to an autonomous player or
adversarial game logic if the game is supposed to reason from fused tracks only.

`POST /api/reset` resets the simulation state.

### Frontend Serving

If `frontend/build` exists, FastAPI serves:

- `/` -> `frontend/build/index.html`
- `/static/*` -> bundled JS/CSS assets
- fallback route -> `index.html`

This is why `npm run build` is enough for localhost to show the latest React UI.

## Frontend: `frontend/src/App.js`

The frontend is a single-file React/canvas tactical display.

### App State

Important React state:

- `stateRef`: latest backend state from WebSocket.
- `connected`: WebSocket link state.
- `hover`: currently hovered sensor/track.
- `pinned`: clicked entity that keeps the inspect panel open.
- `cycle`: latest simulation cycle.

The frontend connects to:

```javascript
ws://localhost:8000/ws
```

On `"update"`, it stores the new backend state and forces a redraw.

### Canvas Rendering

The map bounds are:

```javascript
{ xMin: -50, xMax: 180, yMin: -40, yMax: 120 }
```

Canvas layers:

1. Background.
2. Grid.
3. Sensors.
4. Tracks.

Track rendering uses:

- Amber color for all tracks.
- Shape for class.
- Opacity from `track_confidence`.
- Class label from `leading_class`.
- If `leading_class_prob < 0.15`, map label shows `Unknown`.
- 3-sigma uncertainty ellipse only when hovered or pinned.

Note: the map label uses `leading_class_prob`, which is joint probability. The
inspect panel uses conditional probabilities for category/class display.

### Picking

Mouse picking checks tracks first, then sensors:

- Tracks: within 18 pixels of track position.
- Sensors: within 14 pixels of sensor position.

Only tracks with `track_confidence > 0.05` are pickable/drawn.

### HUD

Top-left HUD shows:

- link online/offline
- cycle
- number of visible tracks
- reset button

Track count uses `track_confidence > 0.05`.

### Legend

Bottom-left legend shows:

- Sensor symbols:
  - radar triangle
  - camera square
  - SIGINT diamond
- Object symbols:
  - SAM Battery diamond
  - Radar Station square
  - Command Post hexagon
  - Vehicle Convoy wedge
  - Infantry circle
  - Aircraft triangle
  - Unknown circle with `?`

### Inspect Panel

Right-side panel opens on hover and pins on click.

Sensor panel:

- sensor ID
- type
- position
- max range
- output type explanation
- current readings attributed to that sensor this cycle

Track panel:

- `Track Confidence` as a large number, no bar
- `Currently tracking` as a fixed-height stable sensor list
- `Last seen`
- tactical `Category`
- `Classification`
- predicted/current position
- error ellipse major/minor
- ellipse orientation

The fixed-height current-sensors block is intentional. It prevents the panel
height from jumping when a sensor starts or stops contributing to the track in
a cycle.

The frontend helper `trackConfidence(track)` prefers `track.track_confidence`
and falls back to legacy `track.existence`.

### Frontend Quirks

- `window.__sensorPipelineState` is used so `SensorCurrentReadings` can inspect
  the latest global state. This is pragmatic, but a cleaner React approach
  would pass state down through props.
- There are some mojibake characters in existing source strings/comments from
  prior encoding handling. They are mostly display artifacts in explanatory
  text and do not affect fusion math.
- There is a dead commented block in `TrackPanel` containing the older bar UI.
  It is harmless but should be removed when doing source cleanup.

## Tests

Run backend tests from repo root:

```powershell
python sensor_pipeline\backend\test_assignment_regressions.py
python sensor_pipeline\backend\test_fusion.py
python sensor_pipeline\backend\test_sensors.py
```

Run frontend build:

```powershell
cd sensor_pipeline\frontend
npm.cmd run build
```

### `test_sensors.py`

Standalone sensor observation smoke test.

Checks:

- Radar outputs bearing/range/RCS/range-rate, no class vector.
- Camera outputs bearing/class likelihood, no range.
- SIGINT outputs bearing/emission/signal strength, no range.
- SIGINT only detects emitting targets.
- Radar detection probability decreases with range and hard-cuts beyond max.

This test prints diagnostic output rather than using `unittest` assertions.

### `test_fusion.py`

Standalone fusion diagnostic.

Scenarios:

- Radar + two SIGINT sensors.
- SIGINT-only bearing triangulation.
- Single SIGINT.
- Single radar.

It prints track confidence/class/position/ellipse diagnostics. The header still
uses older "snapshot" language, but it is useful for quick visual sanity checks.

### `test_assignment_regressions.py`

Actual `unittest` regression suite.

Current tests:

1. Detection probability has hard range cutoff.
2. Incompatible SIGINT emissions do not form a track.
3. Demo scenario does not create high-confidence assignment ghosts.
4. Fixed-site tracks persist with slow uncertainty growth when unseen.

This is the most important test file to keep green during merges.

## Known Technical Caveats

### 1. Some comments are stale

`fusion.py` and `test_fusion.py` still contain some older wording around
"snapshot" and "existence". Current behavior is:

- persistent state
- prediction
- EKF/Kalman update
- mobility-aware confidence decay
- `track_confidence` as the operator-facing name

The stale comments should be cleaned, but they are not functional bugs.

### 2. `existence` remains as an alias

The API still emits `existence` for backward compatibility. New code should use
`track_confidence`.

Similarly:

- `leading_type`
- `leading_type_prob`
- `type_joint_probs`

are legacy aliases for tactical category fields. New code should use:

- `leading_category`
- `leading_category_prob`
- `category_probs_conditional`
- `category_joint_probs`

### 3. Map label probability is joint, inspect probability is conditional

The inspect panel shows category/class percentages conditional on the track
being real.

The compact map label uses `leading_class_prob`, which is joint. This is why the
map label can look lower than the inspect panel class percentage. That is
intentional enough for now, but if it confuses users, rename or adjust the map
label.

### 4. API exposes truth targets

The WebSocket state includes `targets`, and `/api/scenario` returns truth target
positions/classes. This is useful for demo/debugging. Do not feed that truth
field into a strategy agent if the game should use only fused sensor belief.

### 5. Velocity model is simple

The EKF state includes velocity, but simulated targets are currently static.
`range_rate` is emitted as `0.0`, and there is no true target motion model in
the current scenario. The mobility model mainly affects prediction uncertainty
and stale-track persistence.

### 6. Bearing-only ghosts are possible at low confidence

Two bearing-only sensors can triangulate a plausible location. If geometry is
weak, association confidence and no-range localization factor keep confidence
low, but a tentative ghost can still exist. This is operationally reasonable:
it should be treated as "possible emitter intersection", not a confirmed asset.

### 7. Reset does not recreate target RCS

`SimulationState.reset()` resets the engine and cycle but not the scenario
objects. Target RCS stays stable within the server process. This is probably
good for demos, but if you expect full scenario re-randomization, reset needs to
call `create_scenario()` again.

## Merge Checklist

Before merging:

1. Decide whether `frontend/build/` should be committed.
2. Do not commit `frontend/node_modules/`.
3. Do not commit `backend/__pycache__/`.
4. Do not commit runtime logs unless there is a specific reason.
5. Run:

```powershell
python sensor_pipeline\backend\test_assignment_regressions.py
python sensor_pipeline\backend\test_fusion.py
python sensor_pipeline\backend\test_sensors.py
cd sensor_pipeline\frontend
npm.cmd run build
```

6. Start server and confirm:

```powershell
cd sensor_pipeline\backend
python server.py
```

Then open:

```text
http://localhost:8000
```

7. In the UI, sanity-check:

- tracks persist when sensors blink
- fixed-site uncertainty grows slowly
- mobile/air tracks decay faster if unseen
- current sensor list changes without resizing the panel
- camera-less tracks show inferred classification from RCS/SIGINT

## Suggested Next Improvements

These are not required for merge, but they would improve demo quality.

### UI

- Rename `Classification` to `Best Class Estimate` or add an evidence label
  like `Evidence: RCS + SIGINT` so users understand camera-less classification.
- Add evidence badges per track:
  - `RCS`
  - `SIGINT`
  - `EO/IR`
  - `Range Fix`
  - `Bearing Only`
- Add visual stale-state styling:
  - current track: solid
  - predicted track: dashed outline or dimmer label
- Show `current` versus `predicted` more prominently in the inspect panel.
- Remove dead commented JSX and clean encoding artifacts.

### Fusion

- Add true target motion in the simulation and use radar range-rate.
- Implement explicit class-specific motion priors, not just category priors.
- Split track confidence into:
  - real/false-track confidence
  - operational relevance confidence
  - stale confidence
- Add track merge/split handling for long-running ambiguous cases.
- Add a source attribution field for classification, e.g.
  `classification_evidence_sources: ["radar_rcs", "sigint_emission"]`.
- Add sensor health/outage controls for demo scenarios.

### Game Integration

The strategy layer should consume fused belief state, not raw observations.

Recommended strategy-layer inputs:

- track ID
- track confidence
- current/predicted position
- position uncertainty ellipse
- velocity
- current sensor IDs
- cycles since last seen
- category probabilities
- class probabilities
- evidence sources
- dominant emission

The strategy layer should not independently infer track persistence from raw
sensor snapshots unless it has a specific reason to override the fusion layer.

## Important Conceptual Distinctions

These distinctions are central to understanding the code:

### Track Confidence

"Do we believe this is still a real/relevant asset?"

This persists over time and decays when unseen.

### Currently Detected

"Did any sensor contribute an observation to this track this cycle?"

This can flicker because detection is probabilistic.

### Position Uncertainty

"How well do we know where the asset is?"

This is represented by the covariance/error ellipse. It grows when tracks are
not observed and tightens when measurements update the track.

### Classification

"What class do we think it is, assuming the track is real?"

This can come from camera visual likelihood, radar RCS, SIGINT emissions, or a
combination. Camera is not required for classification, but camera-less
classification should be understood as signature-based inference.

### Tactical Category

"What operational mobility/persistence bucket does this likely belong to?"

This is derived from classification probabilities and drives prediction/decay.

## Bottom Line for Merge

The core behavior to preserve is:

- Sensors report partial observations, not omniscient truth.
- FOV is ignored; range is enforced.
- Radar is reliable and range-backed.
- Camera is lower range and less reliable but provides direct visual class
  likelihood.
- SIGINT only sees emitters and provides bearing/emission evidence.
- Same-sensor observations cannot merge into one track in the same cycle.
- Bearing-only observations must respect sensor range.
- Tracks persist through missed detections.
- Fixed sites persist longer and move less than mobile/air tracks.
- Classification evidence is smoothed per sensor and expires when stale.
- The UI displays belief state, not raw truth.

