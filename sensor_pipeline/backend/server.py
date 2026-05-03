"""
FastAPI + WebSocket server for the sensor fusion visualization.

Runs a simulation scenario and streams fused track data to connected clients
over WebSocket.
"""

import asyncio
import json
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from sensors import (
    RadarSensor, CameraSensor, SIGINTSensor,
    Position, Target, Classification, EmissionType
)
from fusion import FusionEngine


# --- Scenario Definition ---

def create_scenario():
    """Create a multi-target scenario with sensors and targets."""
    sensors = [
        RadarSensor("RADAR-01", Position(0, 0), max_range_km=150),
        RadarSensor("RADAR-02", Position(80, 20), max_range_km=120),
        CameraSensor("CAM-01", Position(10, 5), max_range_km=30),
        CameraSensor("CAM-02", Position(55, 45), max_range_km=35),
        SIGINTSensor("SIGINT-01", Position(-20, 40), max_range_km=200),
        SIGINTSensor("SIGINT-02", Position(60, -10), max_range_km=180),
    ]

    targets = [
        Target(
            position=Position(50, 70),
            classification=Classification.SAM_BATTERY,
            emission_type=EmissionType.FIRE_CONTROL,
            is_emitting=True,
            target_id="SAM-01"
        ),
        Target(
            position=Position(20, 15),
            classification=Classification.VEHICLE_CONVOY,
            emission_type=EmissionType.COMMS_VHF,
            is_emitting=True,
            target_id="CONVOY-01"
        ),
        Target(
            position=Position(100, 60),
            classification=Classification.RADAR_STATION,
            emission_type=EmissionType.SEARCH_RADAR,
            is_emitting=True,
            target_id="RADAR-SITE-01"
        ),
        Target(
            position=Position(25, 10),
            classification=Classification.INFANTRY,
            is_emitting=False,
            target_id="INF-01"
        ),
        Target(
            position=Position(70, 85),
            classification=Classification.COMMAND_POST,
            emission_type=EmissionType.COMMS_HF,
            is_emitting=True,
            target_id="CP-01"
        ),
        Target(
            position=Position(130, 40),
            classification=Classification.SAM_BATTERY,
            emission_type=EmissionType.FIRE_CONTROL,
            is_emitting=True,
            target_id="SAM-02"
        ),
    ]

    # Pre-initialize each target's persistent RCS for deterministic scenarios.
    for t in targets:
        t.get_or_init_rcs()
    return sensors, targets


def get_sensor_info(sensors):
    """Get sensor metadata for frontend rendering."""
    info = []
    for s in sensors:
        info.append({
            "sensor_id": s.sensor_id,
            "sensor_type": s.sensor_type.value,
            "position": {"x": s.position.x, "y": s.position.y},
            "max_range_km": s.max_range_km,
        })
    return info


# --- Simulation State ---

class SimulationState:
    def __init__(self):
        self.engine = FusionEngine()
        self.sensors, self.targets = create_scenario()
        self.cycle = 0
        self.running = False

    def reset(self):
        self.engine = FusionEngine()
        self.cycle = 0

    def step(self):
        """Run one observation cycle."""
        t = float(self.cycle)
        observations = []

        for sensor in self.sensors:
            for target in self.targets:
                obs = sensor.observe(target, t)
                if obs:
                    observations.append(obs)

        self.engine.process_observations(observations, t)
        self.cycle += 1

        return self._get_state()

    def _get_state(self):
        """Get current simulation state as JSON-serializable dict."""
        tracks = []
        for track in self.engine.get_all_tracks():
            tracks.append(track.to_dict())

        return {
            "cycle": self.cycle,
            "timestamp": float(self.cycle - 1),
            "tracks": tracks,
            "sensors": get_sensor_info(self.sensors),
            "targets": [
                {
                    "target_id": t.target_id,
                    "position": {"x": t.position.x, "y": t.position.y},
                    "classification": t.classification.value,
                    "is_emitting": t.is_emitting,
                }
                for t in self.targets
            ],
        }


sim = SimulationState()


# --- FastAPI App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="Sensor Fusion Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/scenario")
def get_scenario():
    """Get static scenario info (sensor positions, etc)."""
    return {
        "sensors": get_sensor_info(sim.sensors),
        "targets": [
            {
                "target_id": t.target_id,
                "position": {"x": t.position.x, "y": t.position.y},
                "classification": t.classification.value,
            }
            for t in sim.targets
        ],
    }


@app.post("/api/reset")
def reset_simulation():
    """Reset simulation state."""
    sim.reset()
    return {"status": "reset"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint that streams simulation cycles.
    Sends a new state every 1.5 seconds (simulating sensor refresh rate).
    """
    await websocket.accept()

    try:
        # Send initial scenario info
        await websocket.send_json({
            "type": "scenario",
            "data": {
                "sensors": get_sensor_info(sim.sensors),
            }
        })

        # Reset for fresh start per connection
        sim.reset()
        random.seed(42)  # Reproducible for demo

        # Run simulation cycles
        while True:
            state = sim.step()

            await websocket.send_json({
                "type": "update",
                "data": state,
            })

            # Check for client messages (pause/resume/step)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.5)
                cmd = json.loads(msg)
                if cmd.get("command") == "reset":
                    sim.reset()
                    random.seed(42)
                elif cmd.get("command") == "step":
                    pass  # Already stepped above
            except asyncio.TimeoutError:
                pass  # Normal: just continue to next cycle

    except WebSocketDisconnect:
        pass


# Serve React frontend build (if it exists)
FRONTEND_BUILD = Path(__file__).parent.parent / "frontend" / "build"
if FRONTEND_BUILD.exists():
    @app.get("/")
    async def serve_frontend():
        return FileResponse(FRONTEND_BUILD / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND_BUILD / "static"), name="static")

    @app.get("/{path:path}")
    async def serve_frontend_fallback(path: str):
        file_path = FRONTEND_BUILD / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_BUILD / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
