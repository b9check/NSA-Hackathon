.PHONY: install install-py install-web fetch-satellite sample-terrain dump terrain procedural-terrain assets web dev

install: install-py install-web

install-py:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

install-web:
	cd web && npm install

# 1. Fetch real satellite tiles (Esri World Imagery, cached in /tmp).
fetch-satellite:
	. .venv/bin/activate && python3 scripts/fetch_satellite.py web/public/terrain.png

# 2. Sample the satellite at each hex center and rewrite the scenario YAML
#    so terrain + unit placements match what the eye sees.
sample-terrain:
	. .venv/bin/activate && python3 scripts/sample_terrain.py web/public/terrain.png scenarios/strait_n7.yaml

# 3. Build the wire-format state.json the frontend consumes.
dump:
	. .venv/bin/activate && python3 scripts/dump_state.py scenarios/strait_n7.yaml web/public/state.json

# Full real-imagery pipeline (default region: bonifacio).
terrain: fetch-satellite sample-terrain dump

# One-liner region swap: `make region REGION=aegean` etc.
# Run `make region-list` to see available presets.
REGION ?= bonifacio
region:
	. .venv/bin/activate && python3 scripts/setup_region.py $(REGION)

region-list:
	. .venv/bin/activate && python3 scripts/setup_region.py --list

# Synthetic fallback (no internet): procedural noise-based satellite-look PNG.
procedural-terrain:
	. .venv/bin/activate && python3 scripts/render_terrain.py scenarios/strait_n7.yaml web/public/terrain.png && $(MAKE) sample-terrain dump

assets: terrain

web:
	cd web && npm run dev

dev: assets web
