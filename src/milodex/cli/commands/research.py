"""research command — screening and comparison tooling.

Currently exposes ``research screen``: a batch walk-forward evaluator that
runs every strategy in a given set through the same OOS harness and prints
a ranked comparison table. Intended for working through the research
strategy bank — rather than running ``milodex backtest --walk-forward``
twelve times by hand and eyeballing scrollback, point this at a glob of
configs and read off which candidates clear the gate.

Stays below the governance line: this command only evaluates. It never
freezes, promotes, or advances a strategy. The ``gate`` column is advisory
— the operator still drives any actual promotion via ``milodex promotion
promote`` for each candidate they choose.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from milodex.backtesting.walk_forward_batch import BatchResult, BatchRow, run_batch
from milodex.cli._shared import (
    CommandContext,
    add_global_flags,
    parse_iso_date,
)
from milodex.cli.formatter import CommandResult
from milodex.research.fanout import generate_per_symbol_configs
from milodex.strategies.loader import (
    build_default_registry,
    load_strategy_config,
    resolve_config_path,
)

# ponytail: only truly optional BatchRow keys (those with dataclass defaults)
_BATCH_ROW_DEFAULTS: dict = {
    "oos_equity_curve": [],
    "error": None,
    "survivorship_corrected": False,
}


@dataclass(frozen=True)
class SkippedConfig:
    """A config file matched by a screen glob but intentionally not run."""

    path: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True)
class StrategyDiscovery:
    """Resolved strategy IDs plus audit metadata for glob discovery."""

    strategy_ids: tuple[str, ...]
    matched_config_count: int
    skipped_configs: tuple[SkippedConfig, ...] = ()


def register(subparsers: argparse._SubParsersAction) -> None:
    research = subparsers.add_parser(
        "research",
        help="Research tooling — screen and compare strategy candidates.",
    )
    add_global_flags(research)
    research_sub = research.add_subparsers(dest="research_command", required=True)

    screen = research_sub.add_parser(
        "screen",
        help="Batch walk-forward evaluate a set of strategies and rank results.",
    )
    add_global_flags(screen)
    screen.add_argument(
        "--configs",
        default=None,
        help=(
            "Glob over the configs directory (e.g. 'meanrev_*.yaml'). "
            "Mutually exclusive with --strategy-id."
        ),
    )
    screen.add_argument(
        "--strategy-id",
        action="append",
        dest="strategy_ids",
        default=[],
        help="Strategy id. Repeatable. Mutually exclusive with --configs.",
    )
    screen.add_argument("--start", required=True, help="Screening start date YYYY-MM-DD.")
    screen.add_argument("--end", required=True, help="Screening end date YYYY-MM-DD.")
    screen.add_argument(
        "--fail-fast",
        action="store_true",
        help="Abort on the first strategy error rather than recording it and continuing.",
    )
    screen.add_argument(
        "--initial-equity",
        type=float,
        default=100_000.0,
        help="Per-strategy starting equity.",
    )
    screen.add_argument(
        "--report-out",
        nargs="?",
        const="__default__",
        default=None,
        help=(
            "Write a markdown report (plus JSON sibling). Pass a path, or pass "
            "the flag with no value to use docs/reviews/screen_<today>.md."
        ),
    )
    screen.add_argument(
        "--parallel",
        type=int,
        default=1,
        help=(
            "Number of strategies to backtest in parallel. Default 1 (sequential). "
            "Use a value <= cpu_count() to avoid contention. SQLite is in WAL mode "
            "so concurrent reads + serialized writes are safe."
        ),
    )

    evidence = research_sub.add_parser(
        "evidence",
        help="Assemble an intraday evidence report and write one experiment-registry row.",
    )
    add_global_flags(evidence)
    evidence.add_argument("--candidate-family", required=True, dest="candidate_family")
    evidence.add_argument("--candidate-template", required=True, dest="candidate_template")
    evidence.add_argument("--universe-ref", required=True, dest="universe_ref")
    evidence.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    evidence.add_argument("--end", required=True, help="End date YYYY-MM-DD.")
    evidence.add_argument("--experiment-id", required=True, dest="experiment_id")
    evidence.add_argument("--hypothesis", required=True)
    evidence.add_argument(
        "--screen-json",
        default=None,
        dest="screen_json",
        help="Path to a prior 'research screen --report-out' JSON sibling.",
    )
    # ponytail: lane is permanently IEX — feed label is fixed, not a CLI arg.

    fanout = research_sub.add_parser(
        "fan-out",
        help="Generate one per-symbol config from a base strategy config + universe_ref.",
    )
    add_global_flags(fanout)
    _fanout_id_group = fanout.add_mutually_exclusive_group(required=True)
    _fanout_id_group.add_argument(
        "--strategy-id",
        dest="fanout_strategy_id",
        default=None,
        help="Base strategy id (resolved via configs dir).",
    )
    _fanout_id_group.add_argument(
        "--config",
        dest="fanout_config",
        default=None,
        help="Explicit path to the base strategy config YAML.",
    )
    fanout.add_argument(
        "--universe-ref",
        required=True,
        dest="fanout_universe_ref",
        help="Universe ref string, e.g. 'universe.liquid_etf_core.v1'.",
    )
    fanout.add_argument(
        "--out",
        dest="fanout_out",
        default="configs",
        help="Output directory for generated configs (default: configs).",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.research_command == "screen":
        return _screen(args, ctx)
    if args.research_command == "fan-out":
        return _fanout(args, ctx)
    if args.research_command == "evidence":
        return _evidence(args, ctx)
    msg = f"Unsupported research command: {args.research_command}"
    raise ValueError(msg)


def _batch_result_from_screen_json(path: Path) -> BatchResult:
    """Rehydrate a BatchResult from a 'research screen --report-out' JSON sibling.

    Inverse of _write_report's JSON serialisation. Local to this module — the
    BatchResult dataclass intentionally has no from_dict classmethod (ponytail).
    Tolerates missing optional keys by falling back to _BATCH_ROW_DEFAULTS.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    start_date = date.fromisoformat(data["start_date"])
    end_date = date.fromisoformat(data["end_date"])

    rows: list[BatchRow] = []
    for r in data.get("rows", []):
        raw_curve = r.get("oos_equity_curve", _BATCH_ROW_DEFAULTS["oos_equity_curve"])
        # Curve entries are {"date": "YYYY-MM-DD", "equity": float}
        curve = tuple((date.fromisoformat(pt["date"]), float(pt["equity"])) for pt in raw_curve)
        rows.append(
            BatchRow(
                strategy_id=r["strategy_id"],
                family=r.get("family", ""),
                trade_count=r.get("trade_count", 0),
                oos_sharpe=r.get("oos_sharpe"),
                oos_max_drawdown_pct=r.get("oos_max_drawdown_pct", 0.0),
                oos_total_return_pct=r.get("oos_total_return_pct", 0.0),
                single_window_dependency=r.get("single_window_dependency", False),
                gate_allowed=r.get("gate_allowed", False),
                gate_promotion_type=r.get("gate_promotion_type", ""),
                gate_failures=tuple(r.get("gate_failures", [])),
                run_id=r.get("run_id"),
                oos_equity_curve=curve,
                error=r.get("error", _BATCH_ROW_DEFAULTS["error"]),
                survivorship_corrected=r.get(
                    "survivorship_corrected", _BATCH_ROW_DEFAULTS["survivorship_corrected"]
                ),
            )
        )

    return BatchResult(
        start_date=start_date,
        end_date=end_date,
        rows=tuple(rows),
        correlation_matrix=data.get("correlation_matrix", {}),
    )


def _evidence(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    """Handle ``research evidence``: thin facade over assemble_intraday_evidence."""
    from milodex.research.evidence_assembler import assemble_intraday_evidence

    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)

    batch_result: BatchResult | None = None
    if args.screen_json is not None:
        batch_result = _batch_result_from_screen_json(Path(args.screen_json))

    report, row_id = assemble_intraday_evidence(
        candidate_family=args.candidate_family,
        candidate_template=args.candidate_template,
        universe_ref=args.universe_ref,
        start_date=start,
        end_date=end,
        experiment_id=args.experiment_id,
        hypothesis=args.hypothesis,
        ctx=ctx,
        batch_result=batch_result,
        feed_label="iex",  # ponytail: lane is IEX-only — label is fixed.
    )

    agg = report.aggregate
    verdict = agg.get("verdict", "n/a")
    terminal_status = ctx.get_event_store().get_experiment(args.experiment_id).terminal_status
    n_symbols = len(report.symbols)

    data = report.as_dict()
    data["experiment_registry_row_id"] = row_id

    human_lines = [
        f"Verdict:         {verdict}",
        f"Terminal status: {terminal_status}",
        f"Symbols:         {n_symbols}",
        f"Registry row id: {row_id}",
        "Note: IEX is a single-venue, non-consolidated feed — results are"
        " non-durable (ADR 0017). A decisive win is inconclusive here.",
    ]
    return CommandResult(command="research.evidence", data=data, human_lines=human_lines)


def _fanout(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    """Handle ``research fan-out``: generate per-symbol configs from a base."""
    if args.fanout_config:
        base_path = Path(args.fanout_config)
    else:
        base_path = resolve_config_path(args.fanout_strategy_id, ctx.config_dir)

    out_dir = Path(args.fanout_out)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = generate_per_symbol_configs(
        base_config_path=base_path,
        universe_ref=args.fanout_universe_ref,
        out_dir=out_dir,
    )

    data: dict[str, Any] = {
        "base_config": str(base_path),
        "universe_ref": args.fanout_universe_ref,
        "out_dir": str(out_dir),
        "generated_count": len(written),
        "generated_paths": [str(p) for p in written],
    }
    lines = [
        f"Fan-out: {base_path.name} × {args.fanout_universe_ref}",
        f"Generated {len(written)} config(s) → {out_dir}",
        *[f"  {p.name}" for p in written],
    ]
    return CommandResult(command="research.fan-out", data=data, human_lines=lines)


def _screen(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")

    discovery = _resolve_strategy_ids(args, ctx)

    parallel = max(1, int(getattr(args, "parallel", 1) or 1))
    result = run_batch(
        strategy_ids=discovery.strategy_ids,
        start_date=start,
        end_date=end,
        ctx=ctx,
        fail_fast=args.fail_fast,
        initial_equity=args.initial_equity,
        parallel=parallel,
    )

    report_path: Path | None = None
    if args.report_out is not None:
        report_path = _write_report(args.report_out, result, discovery=discovery)

    data = _build_data(result, report_path=report_path, discovery=discovery)
    lines = _build_human(result, report_path=report_path, discovery=discovery)
    return CommandResult(command="research.screen", data=data, human_lines=lines)


def _resolve_strategy_ids(args: argparse.Namespace, ctx: CommandContext) -> StrategyDiscovery:
    configs = args.configs
    explicit = list(args.strategy_ids or [])
    if configs and explicit:
        raise ValueError("--configs and --strategy-id are mutually exclusive.")
    if not configs and not explicit:
        raise ValueError("Specify strategies via --configs <glob> or --strategy-id <id>.")
    if explicit:
        return StrategyDiscovery(strategy_ids=tuple(explicit), matched_config_count=0)
    matched = sorted(ctx.config_dir.glob(configs))
    if not matched:
        raise ValueError(f"No configs matched {configs!r} under {ctx.config_dir}.")
    ids: list[str] = []
    skipped: list[SkippedConfig] = []
    registry = build_default_registry()
    for path in matched:
        try:
            config = load_strategy_config(path)
        except ValueError:
            # Skip non-strategy YAMLs (e.g. risk_defaults, universe manifests)
            # that don't match the strategy schema.
            skipped.append(SkippedConfig(path=str(path), reason="not_strategy_config"))
            continue
        if registry.resolve(config.family, config.template) is None:
            skipped.append(SkippedConfig(path=str(path), reason="unregistered_strategy"))
            continue
        ids.append(config.strategy_id)
    if not ids:
        raise ValueError(
            f"Glob {configs!r} matched {len(matched)} file(s) but none were runnable "
            "strategy configs."
        )
    return StrategyDiscovery(
        strategy_ids=tuple(ids),
        matched_config_count=len(matched),
        skipped_configs=tuple(skipped),
    )


def _build_data(
    result: BatchResult,
    *,
    report_path: Path | None,
    discovery: StrategyDiscovery,
) -> dict[str, Any]:
    return {
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "row_count": len(result.rows),
        "matched_config_count": discovery.matched_config_count,
        "selected_strategy_ids": list(discovery.strategy_ids),
        "skipped_configs": [item.as_dict() for item in discovery.skipped_configs],
        "rows": [row.as_dict() for row in result.rows],
        "correlation_matrix": result.correlation_matrix,
        "report_path": str(report_path) if report_path else None,
    }


def _build_human(
    result: BatchResult,
    *,
    report_path: Path | None,
    discovery: StrategyDiscovery,
) -> list[str]:
    lines = [
        "Research Screen",
        f"Period:    {result.start_date} to {result.end_date}",
        f"Strategies: {len(result.rows)}",
        "",
    ]
    lines.extend(_format_table(result.rows))
    lines.extend(_format_skipped_configs(discovery.skipped_configs))
    if report_path:
        lines.append("")
        lines.append(f"Report written: {report_path}")
    return lines


_TABLE_HEADER = (
    "strategy_id",
    "family",
    "trades",
    "oos_sharpe",
    "oos_max_dd",
    "fragile",
    "surv_corr",
    "gate",
)


def _format_table(rows: tuple[BatchRow, ...]) -> list[str]:
    data_rows: list[tuple[str, ...]] = [
        (
            row.strategy_id,
            row.family,
            str(row.trade_count),
            _fmt(row.oos_sharpe, ".2f"),
            f"{row.oos_max_drawdown_pct:.2f}%",
            "yes" if row.single_window_dependency else "no",
            "yes" if row.survivorship_corrected else "no",
            _gate_cell(row),
        )
        for row in rows
    ]
    widths = [len(h) for h in _TABLE_HEADER]
    for cells in data_rows:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    header = "  ".join(h.ljust(widths[i]) for i, h in enumerate(_TABLE_HEADER))
    separator = "  ".join("-" * w for w in widths)
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) for cells in data_rows]
    return [header, separator, *body]


def _gate_cell(row: BatchRow) -> str:
    if row.error:
        return "error"
    if row.gate_allowed:
        return f"pass ({row.gate_promotion_type})"
    return "block"


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _format_skipped_configs(skipped_configs: tuple[SkippedConfig, ...]) -> list[str]:
    if not skipped_configs:
        return []
    lines = ["", f"Skipped configs: {len(skipped_configs)}"]
    for item in skipped_configs:
        lines.append(f"- {item.path} ({item.reason})")
    return lines


def _write_report(target: str, result: BatchResult, *, discovery: StrategyDiscovery) -> Path:
    stem_date = date.today().isoformat()
    if target == "__default__":
        path = Path("docs/reviews") / f"screen_{stem_date}.md"
    else:
        path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_markdown(result, discovery=discovery), encoding="utf-8")
    json_path = path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "start_date": result.start_date.isoformat(),
                "end_date": result.end_date.isoformat(),
                "generated_at": datetime.now().astimezone().isoformat(),
                "matched_config_count": discovery.matched_config_count,
                "selected_strategy_ids": list(discovery.strategy_ids),
                "skipped_configs": [item.as_dict() for item in discovery.skipped_configs],
                "rows": [row.as_dict() for row in result.rows],
                "correlation_matrix": result.correlation_matrix,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _render_markdown(result: BatchResult, *, discovery: StrategyDiscovery) -> str:
    header_cells = list(_TABLE_HEADER)
    separator = ["---"] * len(header_cells)
    rows_md: list[str] = []
    for row in result.rows:
        rows_md.append(
            "| "
            + " | ".join(
                [
                    f"`{row.strategy_id}`",
                    row.family or "—",
                    str(row.trade_count),
                    _fmt(row.oos_sharpe, ".2f"),
                    f"{row.oos_max_drawdown_pct:.2f}%",
                    "yes" if row.single_window_dependency else "no",
                    "yes" if row.survivorship_corrected else "no",
                    _gate_cell(row),
                ]
            )
            + " |"
        )
    body = [
        f"# Research Screen — {result.start_date} → {result.end_date}",
        "",
        f"Generated: {datetime.now().astimezone().isoformat()}",
        f"Strategies: {len(result.rows)}",
        "",
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(separator) + " |",
        *rows_md,
        "",
        *_render_skipped_configs_markdown(discovery.skipped_configs),
        "## OOS Return Correlation Matrix",
        "",
        *_render_correlation_matrix(result),
        "",
        "## Per-strategy detail",
        "",
    ]
    for row in result.rows:
        body.append(f"### `{row.strategy_id}`")
        body.append("")
        if row.error:
            body.append(f"- **ERROR:** {row.error}")
            body.append("")
            continue
        body.append(f"- Family: {row.family}")
        body.append(f"- Trades: {row.trade_count}")
        body.append(f"- OOS Sharpe: {_fmt(row.oos_sharpe, '.4f')}")
        body.append(f"- OOS Max DD: {row.oos_max_drawdown_pct:.2f}%")
        body.append(f"- OOS Total Return: {row.oos_total_return_pct:+.2f}%")
        body.append(f"- Single-window dependency: {row.single_window_dependency}")
        body.append(f"- Survivorship-corrected universe: {row.survivorship_corrected}")
        body.append(f"- Gate: {row.gate_promotion_type} — allowed={row.gate_allowed}")
        if row.gate_failures:
            body.append("- Gate failures:")
            for failure in row.gate_failures:
                body.append(f"  - {failure}")
        body.append(f"- Run ID: {row.run_id}")
        body.append("")
    return "\n".join(body)


def _render_skipped_configs_markdown(skipped_configs: tuple[SkippedConfig, ...]) -> list[str]:
    if not skipped_configs:
        return []
    lines = [
        "## Skipped configs",
        "",
        "| path | reason |",
        "| --- | --- |",
    ]
    for item in skipped_configs:
        lines.append(f"| `{item.path}` | {item.reason} |")
    lines.append("")
    return lines


def _render_correlation_matrix(result: BatchResult) -> list[str]:
    strategy_ids = [row.strategy_id for row in result.rows]
    if not strategy_ids:
        return ["No strategies screened."]
    header = ["strategy", *strategy_ids]
    separator = ["---"] * len(header)
    rows = ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
    for left in strategy_ids:
        cells = [f"`{left}`"]
        for right in strategy_ids:
            value = result.correlation_matrix.get(left, {}).get(right)
            cells.append("n/a" if value is None else f"{value:.2f}")
        rows.append("| " + " | ".join(cells) + " |")
    return rows
