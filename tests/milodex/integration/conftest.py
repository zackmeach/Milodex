"""Shared fixtures for the integration drills.

The queue-at-open drill reuses the runner harness's ``strategy_config_dir`` and
``risk_defaults_file`` fixtures. Re-exporting them here (rather than importing
them into the test module) keeps pytest's fixture resolution clean — importing
a fixture into a test module triggers an F811 redefinition against the test's
own parameter of the same name.
"""

from __future__ import annotations

from tests.milodex.strategies.test_runner import (
    risk_defaults_file,
    strategy_config_dir,
)

__all__ = ["risk_defaults_file", "strategy_config_dir"]
