#!/usr/bin/env bash
# Daily dump of the paper database (spec 11), 14 days kept. The live backup is separate.
set -euo pipefail
dir="$HOME/paper/backups"
mkdir -p "$dir"
docker exec signalbench-postgres-1 pg_dump -U signalbench -d signalbench_paper -Fc \
  > "$dir/signalbench_paper-$(date -u +%Y%m%d).dump"
find "$dir" -name 'signalbench_paper-*.dump' -mtime +14 -delete
