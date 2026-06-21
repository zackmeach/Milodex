"""Structural separation tests for the broker layer.

R-BRK-006: The broker module shall not contain any strategy, risk, or analytics
logic. Acceptance: no imports from milodex.strategies, milodex.risk,
milodex.execution, or milodex.analytics inside broker/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# tests/milodex/broker/ -> repo root -> src/milodex/broker.
_BROKER_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "broker"

_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "from milodex.strategies",
    "import milodex.strategies",
    "from milodex.risk",
    "import milodex.risk",
    "from milodex.execution",
    "import milodex.execution",
    "from milodex.analytics",
    "import milodex.analytics",
)


def _broker_source_files() -> list[Path]:
    return sorted(_BROKER_SRC.glob("*.py"))


@pytest.mark.parametrize("src_file", _broker_source_files(), ids=lambda p: p.name)
def test_broker_file_contains_no_forbidden_imports(src_file: Path) -> None:
    """R-BRK-006: broker source must not import strategy, risk, execution, or analytics.

    Fails if any broker/*.py file imports milodex.strategies, milodex.risk,
    milodex.execution, or milodex.analytics.
    """
    for line in src_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for forbidden in _FORBIDDEN_PREFIXES:
            assert not stripped.startswith(forbidden), (
                f"{src_file.name} must not import {forbidden!r} (R-BRK-006). Line: {line!r}"
            )
