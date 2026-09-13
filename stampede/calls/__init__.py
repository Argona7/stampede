"""STAMPEDE Calls: the Telegram poster of the engine's `edge_enter` alerts (docs/CALLS.md).

`format.py` turns an alert / outcome / paper exit / track record into the HTML text of one message; `poster.py`
consumes `GET /api/stream` (SSE, resume by `Last-Event-ID`), applies the rule of `stampede/signals/calls-config.json`,
posts through the Bot API and remembers every decision in the `calls` table of the live store so a restart never
posts twice.
"""
