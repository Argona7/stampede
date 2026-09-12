"""Realtime engine: free websocket feed -> in-memory state -> incremental sequences / radar -> SSE.

Modules: `feed` (websocket client + block assembly + gap backfill), `state` (registry, curve reserves, per-wallet
rings, rolling per-coin windows, `apply_block`), `runner` (threads, writer, alerts, the `Engine` object the API
process starts), `bus` (SSE ring + latency histograms), `seed` (copy a registry from another store).
The event contract is documented in docs/ENGINE.md.
"""
