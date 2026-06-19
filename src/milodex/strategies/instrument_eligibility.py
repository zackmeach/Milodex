"""Phase-1 instrument eligibility — reject leveraged/inverse/volatility ETPs.

ADR 0016 declares the Phase-1 whitelist (long-only U.S. common stock + plain-
vanilla ETFs) and *claims* (ADR 0016:42) the universe-manifest load enforces it.
It did not: the prose described enforcement that no code implemented. This module
makes the enforcement real at the universe-resolution chokepoints in
``strategies/loader.py``.

Mechanism: a hardcoded curated DENYLIST of known leveraged (2x/3x/-1x/-3x),
inverse, and volatility ETPs. No instrument-type metadata exists on symbols in
Phase 1 (no SIP / reference feed — ADR 0017), so an allowlist-by-type is not
feasible yet.

# ponytail: curated denylist of well-known leveraged/inverse/vol ETPs — NOT
# exhaustive. A newly-launched leveraged ETP not on this list would pass. Upgrade
# path: replace with an instrument-type allowlist when a reference-data feed
# (SIP / Massive, ADR 0017) supplies asset_class / leverage metadata.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Known leveraged / inverse / volatility ETPs forbidden by ADR 0016. Uppercase;
#: matched case-insensitively by exact symbol (so SHY != SH, SLV != SVXY).
#: Curated, not exhaustive (see module docstring).
FORBIDDEN_ETP_SYMBOLS: frozenset[str] = frozenset(
    {
        # Leveraged long broad-index
        "SSO", "UPRO", "SPXL", "QLD", "TQQQ", "UDOW", "UWM", "URTY", "DDM",
        # Inverse / leveraged-inverse broad-index
        "SH", "SDS", "SPXU", "SPXS", "PSQ", "QID", "SQQQ", "DOG", "DXD",
        "SDOW", "RWM", "TWM", "TZA", "TNA",
        # Sector / thematic leveraged + inverse
        "SOXL", "SOXS", "FAS", "FAZ", "LABU", "LABD", "TECL", "TECS",
        "NUGT", "DUST", "JNUG", "JDST", "ERX", "ERY", "GUSH", "DRIP",
        "YINN", "YANG", "BOIL", "KOLD", "UCO", "SCO",
        # Leveraged bonds
        "TMF", "TMV", "TBT", "UBT", "TYD", "TYO",
        # Volatility ETPs (decay breaks naive backtest — ADR 0016:34)
        "VXX", "VIXY", "UVXY", "SVXY", "VIXM", "SVIX", "UVIX", "VXZ",
    }
)


class InstrumentEligibilityError(ValueError):
    """Raised when a universe contains an instrument forbidden by ADR 0016.

    Subclasses ``ValueError`` so existing config-load error handling (which
    catches ``ValueError`` — e.g. ``loader.resolve_config_path``) treats an
    ineligible universe like any other invalid config. The trade-off: a forbidden
    config is *skipped* by glob loaders rather than failing loudly. The loud
    surfaces that DO fail are the no-try/except ``load_strategy_config`` calls in
    the test suite (``tests/.../test_loader.py``) and the CLI handlers, which
    catch this subclass explicitly to emit a distinct error.
    """


def reject_ineligible_instruments(symbols: Iterable[str], *, source: str) -> None:
    """Raise :class:`InstrumentEligibilityError` if any symbol is a forbidden ETP.

    ``source`` names the manifest / universe for the error message. Symbols are
    upper-cased and matched against :data:`FORBIDDEN_ETP_SYMBOLS` by exact
    membership. Empty input is a no-op.
    """
    forbidden = sorted(
        s
        for s in {str(sym).strip().upper() for sym in symbols}
        if s in FORBIDDEN_ETP_SYMBOLS
    )
    if forbidden:
        msg = (
            f"{source}: universe contains instrument(s) forbidden by ADR 0016 "
            f"(leveraged/inverse/volatility ETP): {', '.join(forbidden)}. "
            f"Phase 1 trades long-only common stock and plain-vanilla ETFs only."
        )
        raise InstrumentEligibilityError(msg)
