"""Per-symbol config generator for cross-ETF evidence fan-out (PR-0A).

Given a single-symbol base strategy config and a universe_ref, writes one
YAML config per symbol in the resolved universe, skipping the base's own
variant symbol (which the base config already represents).

ponytail: inline ``universe:[SYM]`` over minting per-symbol manifests — the
ADR-0016 eligibility guard still fires on inline universes (loader.py:512).
The generator is intentionally a single function returning written paths; all
business rules live here, the CLI is a thin wrapper.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from milodex.strategies.loader import load_strategy_config, resolve_universe_ref


def generate_per_symbol_configs(
    *,
    base_config_path: Path,
    universe_ref: str,
    out_dir: Path,
) -> list[Path]:
    """Generate one single-symbol config per non-base symbol in ``universe_ref``.

    Reads the base config at ``base_config_path``, resolves the universe via
    ``resolve_universe_ref`` (which also runs the ADR-0016 eligibility guard),
    then deep-copies the base YAML once per symbol — skipping the symbol whose
    ``lower()`` matches the base variant — and writes to
    ``out_dir/<stem>_<sym-lower>_v<version>.yaml``.

    The base config is never overwritten.  Returns the list of written paths.

    Raises ``ValueError`` if the base variant symbol is not in the resolved
    universe (base config is mis-paired with this universe_ref).
    """
    base_config = load_strategy_config(base_config_path)

    family = base_config.family
    template = base_config.template
    variant = base_config.variant  # e.g. "spy"
    version = base_config.version  # e.g. 1

    # Resolve the universe — runs ADR-0016 ineligibility guard.
    symbols = resolve_universe_ref(universe_ref, base_config_path)

    # Assert the base variant is actually in the resolved universe.
    if variant.lower() not in {s.lower() for s in symbols}:
        msg = (
            f"Base config variant '{variant}' is not in universe '{universe_ref}' "
            f"({sorted(symbols)}); base config is mis-paired with this universe_ref."
        )
        raise ValueError(msg)

    # Derive the output stem by stripping the trailing _<basevariant>_v<version>
    # segment from the base filename.  E.g. "meanrev_rsi2_intraday_spy_v1" →
    # "meanrev_rsi2_intraday".
    base_stem = base_config_path.stem  # e.g. "meanrev_rsi2_intraday_spy_v1"
    suffix_to_strip = f"_{variant.lower()}_v{version}"
    if base_stem.endswith(suffix_to_strip):
        stem = base_stem[: -len(suffix_to_strip)]
    else:
        # Fallback: strip only the trailing _v<version> segment if variant not found.
        stem = base_stem

    # Load raw YAML once for deep-copying.
    with base_config_path.open("r", encoding="utf-8") as fh:
        raw_base: dict = yaml.safe_load(fh)

    written: list[Path] = []
    for sym in symbols:
        if sym.lower() == variant.lower():
            # Skip the base's own symbol — its config already exists.
            continue

        sym_lower = sym.lower()
        data = copy.deepcopy(raw_base)
        strategy_section = data["strategy"]

        # Set variant and id using the loaded fields verbatim (template is dotted,
        # e.g. "rsi2.intraday" — do NOT string-split the base id).
        new_variant = sym_lower
        new_id = f"{family}.{template}.{new_variant}.v{version}"
        strategy_section["variant"] = new_variant
        strategy_section["id"] = new_id

        # Replace universe_ref with an inline single-symbol universe list.
        # ponytail: inline universe over per-symbol manifests; eligibility guard
        # fired on the full universe at resolve_universe_ref call above.
        strategy_section.pop("universe_ref", None)
        strategy_section["universe"] = [sym.upper()]

        # Swap the base-variant ticker token in the description so the generated
        # config names the symbol it actually trades.
        # ponytail: plain token-swap on description — safe because base descriptions
        # reference the instrument by ticker only (e.g. "SPY"), never as a substring
        # of another word.
        base_sym_upper = variant.upper()
        if "description" in strategy_section and base_sym_upper in strategy_section["description"]:
            strategy_section["description"] = strategy_section["description"].replace(
                base_sym_upper, sym.upper()
            )

        out_path = out_dir / f"{stem}_{sym_lower}_v{version}.yaml"
        with out_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

        written.append(out_path)

    return written
