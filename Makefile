.PHONY: setup dev db api web test lint eval eval-smoke replay

setup: db
	cd backend && uv sync
	cd frontend && npm install
	cd backend && uv run parley db init

# Everything at once: Postgres, API, console. Ctrl+C to stop.
dev:
	./scripts/dev.sh

db:
	./scripts/db.sh start

api:
	cd backend && uv run parley serve --reload

web:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests
	cd frontend && npm run build

# Full release gate (calls the Claude API).
eval:
	cd backend && uv run parley eval run

eval-smoke:
	cd backend && uv run parley eval run --tag smoke

replay:
	cd backend && uv run parley eval replay --last 20
