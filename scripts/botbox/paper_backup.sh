#!/usr/bin/env bash
# Daily dump of the paper database (spec 11), 14 days kept. The live backup is separate.
set -euo pipefail
dir="$HOME/paper/backups"
mkdir -p "$dir"
file="$dir/signalbench_paper-$(date -u +%Y%m%d).dump"
# Dump to a temporary name: a failed pg_dump (set -e stops before the mv) never leaves a
# partial dump under the final name.
docker exec signalbench-postgres-1 pg_dump -U signalbench -d signalbench_paper -Fc \
  > "$file.tmp"
mv "$file.tmp" "$file"
find "$dir" -name 'signalbench_paper-*.dump' -mtime +14 -delete
find "$dir" -name 'signalbench_paper-*.tmp' -mtime +1 -delete
