#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
case "${1:-}" in
  sync-cdr|nightly)
    # Lock on the host before starting a container: safe across cron overlaps.
    exec flock -n "/var/lock/osum-cdr-$1.lock" docker compose run --rm -T --no-deps web flask --app app "$1"
    ;;
  renew-certificate)
    docker compose run --rm -T certbot renew --quiet
    docker compose exec -T nginx nginx -s reload
    ;;
  *) echo 'Usage: run-job.sh sync-cdr|nightly|renew-certificate' >&2; exit 2 ;;
esac
