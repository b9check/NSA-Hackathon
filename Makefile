.PHONY: install install-py install-web dump dev web

install: install-py install-web

install-py:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

install-web:
	cd web && npm install

dump:
	. .venv/bin/activate && python3 scripts/dump_state.py scenarios/strait_n7.yaml web/public/state.json

web:
	cd web && npm run dev

dev: dump web
