# poe2-cx

A little dashboard for the Path of Exile 2 Currency Exchange — see what's liquid,
what each currency trades at, and how rates moved over the day, so you can size up
trades before opening the in-game panel.

![screenshot](screenshot.png)

## Data

Currently pulls from [poe2scout](https://poe2scout.com), which re-serves the
official Currency Exchange data with a delay. Limitations:

- one rate per pair, not the real bid/ask spread (`lowest_ratio` / `highest_ratio`)
- ~1–3 h behind the live hour
- hourly bars, no individual trades

A future version will support the official Currency Exchange API (`service:cxapi`)
for the full per-pair bid/ask range. It isn't open by default — you request it
from GGG (a personal, standalone client is fine: `oauth@grindinggear.com`).

## Run

    python -m cx              # GUI
    python -m cx --once       # one headless pull
    python -m cx.backfill     # backfill last 24h
