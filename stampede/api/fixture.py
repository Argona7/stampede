"""`stampede export-fixture`: dump the API responses for the recorded sample to one JSON file
(status + graph for the whole sample + evidence for the top edges). Useful for sharing a static snapshot;
the terminal itself talks to the API."""
from __future__ import annotations

import json
from pathlib import Path

from ..env import ROOT
from ..rotation import parse_window
from ..store import Store
from . import queries


def main_export(args) -> int:
    store = Store()
    w = parse_window(args.window)
    smp = queries.sample(store)
    g = queries.graph(store, w, smp["from_ts"], smp["to_ts"], 1, 300)
    edges = {}
    for e in g["edges"][:40]:
        edges[f"{e['from']}->{e['to']}"] = queries.edge(store, e["from"], e["to"], w, smp["from_ts"], smp["to_ts"], 30)
    out = {"mode": "fixture", "sample": smp, "coverage": queries.coverage_summary(store), "window_s": w, "graph": g, "edges": edges}
    p = Path(args.out)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    print(f"wrote {p} ({p.stat().st_size / 1e6:.1f} MB)")
    return 0
