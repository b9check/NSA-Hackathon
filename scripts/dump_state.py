"""Load a scenario YAML and write the initial GameState as JSON.

Usage:
    python3 scripts/dump_state.py scenarios/strait_n7.yaml web/public/state.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the project root importable when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.scenario import load_scenario  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: dump_state.py SCENARIO.yaml OUT.json", file=sys.stderr)
        return 2
    scenario_path, out_path = sys.argv[1], sys.argv[2]
    state = load_scenario(scenario_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state.model_dump(mode="json"), indent=2))
    print(f"wrote {out} ({len(state.units)} units, "
          f"{state.map.cols}x{state.map.rows} hexes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
