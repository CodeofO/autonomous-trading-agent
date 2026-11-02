.PHONY: install dev backend frontend test test-backend test-frontend e2e lint format migrate up down

install:
	cd backend && uv sync --frozen
	cd frontend && npm ci --no-audit --no-fund

migrate:
	cd backend && uv run alembic upgrade head

backend:
	cd backend && uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

# 백엔드 + 프론트 동시 실행 (Ctrl+C 로 둘 다 종료)
dev:
	./run.sh local

test: test-backend test-frontend

test-backend:
	cd backend && uv run pytest --cov=app --cov-report=term-missing:skip-covered

test-frontend:
	cd frontend && npm test

e2e:
	cd frontend && npx playwright install chromium && npm run test:e2e

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd frontend && npm run lint

format:
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && npm run format

up:
	docker compose up --build

down:
	docker compose down
