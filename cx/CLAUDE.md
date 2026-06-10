# cx — tiket_master consumer (POE2)

cx mounts the universal **tiket_master** kit (`C:\TP3\tiket_master`) for its
point-at-a-widget + ticket loop. `cx/__init__` puts `C:\TP3` on `sys.path`, so
`from tiket_master import mount` resolves; the lane is `cx_tickets.md` at the
repo root (`C:\POE\POE2`).

## Element references — grep the eid
A ticket may carry a token like `⟦uniques.groupbar⟧` or `⟦board.tree[Divine Orb]⟧`.
The `_eid` inside is **literal in cx's source** — grep it under `cx/ui/` to find
the exact widget (`uniques.*` → `cx/ui/uniques_panel.py`, `board.*` / currency →
`cx/ui/cx_panel.py`, `trade.*` → `cx/ui/trade_panel.py`). A `▸` in a token means
a descendant of that eid; the eid + text still grep back to the region.

## Discipline — saturate the kit, never fork it
A cx panel adds only its own widget tagging + one `mount()` call + its toolbar
button; it never edits a tiket_master brick. Additive extension is fine; changing
a brick's behaviour is a seam failure. Full kit contract: `C:\TP3\tiket_master\CLAUDE.md`.

## Run / check
- The Python with tkinter is the venv `C:\Pasha an\venv` — not system Python.
- Sanity-check edits with `py_compile`; **the user runs the GUI** (don't launch it).
- Ticket workflow: run `/doc-ticket` from a session whose cwd is `C:\POE\POE2`.
