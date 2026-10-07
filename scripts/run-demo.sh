#!/bin/sh
set -u

restore_worker() {
    docker compose start worker >/dev/null
}

trap restore_worker EXIT INT TERM
docker compose stop worker
docker compose run --rm api python manage.py demo_scenario
