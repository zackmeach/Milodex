"""Shared fixtures for the strategies test package.

Re-exports the runner test harness's config fixtures so sibling modules (e.g.
``test_runner_queued_intent_persist.py``) can request them by name. Pytest only
shares fixtures across modules via ``conftest.py``, not via direct imports
between sibling test modules.
"""

from __future__ import annotations

from tests.milodex.strategies.test_runner import (
    risk_defaults_file,  # noqa: F401 — re-exported pytest fixture
    strategy_config_dir,  # noqa: F401 — re-exported pytest fixture
)
