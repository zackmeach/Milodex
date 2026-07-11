"""M4 fault-injection drill harness.

A standalone, subprocess-driven harness that injects a real fault into a
throwaway scratch environment and asserts against the real operator surface
(actual ``python -m milodex.cli.main`` output and/or actual event-store rows).
Unit-test coverage is deliberately *not* the pass criterion — each cell drives
the real CLI and the real durable store.

Entry point: ``python scripts/drills/run_drills.py`` (or
``python -m scripts.drills.run_drills``).
"""
