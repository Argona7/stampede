#!/bin/sh
# Resume the research pipeline after the log fetch: registry, anchors, normalize, rotate.
cd /Users/argona/dev/stampede
export STAMPEDE_DB=data/research-12h.sqlite
echo "== registry $(date -u)"
.venv/bin/python - <<'PY' || exit 1
import json
from stampede.store import Store
from stampede.rpc import Rpc
from stampede.hypersync import HyperSync
from stampede.ingest import Ingest
store = Store(); rpc = Rpc(); ing = Ingest(store, rpc, HyperSync(), workers=4)
fr, to = store.db.execute("SELECT MIN(block), MAX(block) FROM logs").fetchone()
print("range", fr, to, flush=True)
print("anchors", ing.anchor_blocks(fr, to), flush=True)
print("curves", ing.resolve_curves(), flush=True)
print("pools", ing.resolve_pools(), flush=True)
print("token_meta", ing.resolve_token_meta(), flush=True)
print("quotes", ing.resolve_quotes(), flush=True)
store.set_meta("sample_from_block", fr); store.set_meta("sample_to_block", to); store.set_meta("sample_label", f"research 5h blocks {fr}-{to}")
store.commit()
PY
echo "== normalize $(date -u)"
.venv/bin/python -m stampede normalize || exit 1
echo "== rotate 30m $(date -u)"
.venv/bin/python -m stampede rotate --window 30m || exit 1
echo "== rotate 5m $(date -u)"
.venv/bin/python -m stampede rotate --window 5m || exit 1
echo "== lifecycle $(date -u)"
.venv/bin/python -m stampede sync-lifecycle || exit 1
echo "== done $(date -u)"
