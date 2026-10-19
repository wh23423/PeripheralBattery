"""Peripheral Battery collector (M0).

Collects battery levels from the upstream ``providers/`` package and speaks the
NDJSON protocol in ``protocol.md`` over stdio:

    providers -> threads -> complete-snapshot StateStore -> protocol envelopes
                        -> stdout (JSONL) / stdin (commands)

Entry point: ``python -m collector --stdio`` (see ``__main__.py``).
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
