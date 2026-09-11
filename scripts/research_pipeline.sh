#!/bin/sh
cd /Users/argona/dev/stampede
export STAMPEDE_DB=data/research-12h.sqlite
echo "== ingest $(date -u)"
.venv/bin/python -m stampede ingest --from-block 60018681 --to-block 60198681 --label "research 5h ending block 60198681" --chunk 300 || exit 1
echo "== normalize $(date -u)"
.venv/bin/python -m stampede normalize || exit 1
echo "== rotate 30m $(date -u)"
.venv/bin/python -m stampede rotate --window 30m || exit 1
echo "== rotate 5m $(date -u)"
.venv/bin/python -m stampede rotate --window 5m || exit 1
echo "== done $(date -u)"
