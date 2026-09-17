.PHONY: install lint format typecheck test check demo record clean ui web-install build-web check-web e2e

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck test

ui:
	uv run rewyn ui

# npm ci, not npm install: the bundle under src/rewyn/ui/static is committed
# and CI diffs it against a fresh build. npm install is free to move versions
# inside their ranges, which changes chunk content hashes and fails that check
# even though nothing in the console changed. ci installs the lockfile exactly.
web-install:
	cd web && npm ci

# Build the console once; the wheel ships the result under rewyn/ui/static.
build-web: web-install
	cd web && npm run build
	rm -rf src/rewyn/ui/static
	cp -r web/out src/rewyn/ui/static

check-web: web-install
	cd web && npm run format:check && npm run lint && npm run typecheck && npm run test -- --run

# Drives the real console (Python server + built bundle) in a browser.
e2e: build-web
	cd web && npx playwright install --with-deps chromium && npm run e2e

demo:
	uv run python -m demo

# Re-record after any change that alters the demo's output. The demo test
# tells you when that has happened. Needs: brew install asciinema agg
record:
	REWYN_HOME=$$(mktemp -d)/.rewyn DEMO_SPEED=1 DEMO_COLOUR=1 PYTHONUNBUFFERED=1 \
	  asciinema rec demo/rewyn-demo.cast --window-size 100x32 --overwrite \
	  -c "uv run python -m demo"
	agg --theme asciinema --font-size 15 --speed 1.8 --idle-time-limit 0.9 \
	  --fps-cap 10 demo/rewyn-demo.cast demo/rewyn-demo.gif 2>/dev/null
	@ls -lh demo/rewyn-demo.gif | awk '{print "gif:", $$5}'

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov dist build
