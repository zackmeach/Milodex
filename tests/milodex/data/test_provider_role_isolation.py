"""Provider role-interface isolation for data consumers.

R-DAT-007: strategies/, execution/, backtesting/, and analytics/ reference only
the DataProvider ABC, never a concrete vendor provider (AlpacaDataProvider,
YahooProvider, etc.). The three-named-roles config is deferred (ADR 0017); this
test pins the testable half of the acceptance criteria: no concrete-provider
import in the consuming packages.

backtesting/walk_forward_batch.py is the one allowlisted exception — its
multiprocessing workers re-import AlpacaDataProvider because a provider cannot be
inherited across the pickling boundary (see the comment in that file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# tests/milodex/data/ -> repo root -> src/milodex.
_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "milodex"

_CONSUMER_PACKAGES = ("strategies", "execution", "analytics", "backtesting")

# Concrete-provider tokens that must not appear in a consumer import statement.
_CONCRETE_PROVIDER_TOKENS = (
    "alpaca_provider",
    "yahoo_provider",
    "AlpacaDataProvider",
    "YahooProvider",
)

# Consumer files allowed to import a concrete provider (path relative to src/milodex).
_ALLOWLIST = frozenset({"backtesting/walk_forward_batch.py"})


def _consumer_files() -> list[Path]:
    files: list[Path] = []
    for pkg in _CONSUMER_PACKAGES:
        files.extend(sorted((_SRC_ROOT / pkg).rglob("*.py")))
    return files


@pytest.mark.parametrize(
    "src_file",
    _consumer_files(),
    ids=lambda p: p.relative_to(_SRC_ROOT).as_posix(),
)
def test_consumer_imports_only_provider_role_interface(src_file: Path) -> None:
    """R-DAT-007: a data consumer must not import a concrete provider (allowlist aside)."""
    rel = src_file.relative_to(_SRC_ROOT).as_posix()
    offending = [
        line.strip()
        for line in src_file.read_text(encoding="utf-8").splitlines()
        if (line.strip().startswith(("from ", "import ")))
        and any(token in line for token in _CONCRETE_PROVIDER_TOKENS)
    ]
    if offending:
        assert rel in _ALLOWLIST, (
            f"R-DAT-007: {rel} imports a concrete data provider ({offending!r}); "
            "consumers must depend on the DataProvider ABC only."
        )
