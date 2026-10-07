.PHONY: setup up down logs migrate check lint format test worker-once seed demo api-smoke

setup:
	@docker info >/dev/null 2>&1 || (echo "Docker daemon unavailable. Start Docker Desktop and retry." >&2; exit 1)
	@python3 scripts/prepare-env.py
	docker compose build
	docker compose up -d --wait db
	$(MAKE) migrate
	$(MAKE) seed

up:
	docker compose up -d --wait api worker

down:
	docker compose down

logs:
	docker compose logs -f api worker

migrate:
	docker compose run --rm api python manage.py migrate --database default --noinput
	docker compose run --rm api python manage.py migrate --database payments --noinput

check:
	docker compose run --rm api python manage.py check
	docker compose run --rm api python manage.py makemigrations --check --dry-run

lint:
	docker compose run --rm api ruff check src tests manage.py scripts
	docker compose run --rm api ruff format --check src tests manage.py scripts

format:
	docker compose run --rm api ruff format src tests manage.py scripts

test:
	docker compose run --rm api python manage.py test tests --settings=config.test_settings

worker-once:
	docker compose run --rm worker python manage.py run_worker --once

seed:
	docker compose run --rm api python manage.py seed_demo

demo:
	sh scripts/run-demo.sh

api-smoke:
	sh scripts/api-smoke.sh
