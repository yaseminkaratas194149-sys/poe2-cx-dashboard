"""PoE2 currency-exchange app — a separate vertical on the DATA framework.

Importing the package puts the DATA framework root (C:\\TP3) on sys.path so
`import DATA.pipeline...` resolves. cx keeps its own window, its own Postgres
schemas (cx_<league>), and does not use DATA's Collector subsystem.
"""
import sys as _sys

_TP_ROOT = r"C:\TP3"
if _TP_ROOT not in _sys.path:
    _sys.path.insert(0, _TP_ROOT)
