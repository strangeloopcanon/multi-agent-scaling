from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt


@dataclass(frozen=True)
class Totals:
    tasks: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    total_calls: int

def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _base_task_id(task_id: str) -> str:
    value = str(task_id)
    if "_" not in value:
        return value
    prefix, rest = value.split("_", 1)
    if prefix.isdigit():
        return rest
    return value


def _aggregate(rows: list[dict]) -> Totals:
    tasks = len(rows)
    input_tokens = sum(int(r.get("total_input_tokens") or 0) for r in rows)
    output_tokens = sum(int(r.get("total_output_tokens") or 0) for r in rows)
    total_calls = sum(int(r.get("total_calls") or 0) for r in rows)
    return Totals(
        tasks=tasks,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        total_calls=total_calls,
    )


def _fmt_int(value: int) -> str:
    return f"{int(value):,}"


def _fmt_float(value: float, digits: int = 1) -> str:
    return f"{float(value):,.{digits}f}"


def _build_table_rows(
    *,
    market: Totals,
    solo: Totals,
    market_passes: int,
    solo_passes: int,
    common_count: int,
) -> list[list[str]]:
    market_rate = (market_passes / common_count) if common_count > 0 else 0.0
    solo_rate = (solo_passes / common_count) if common_count > 0 else 0.0
    return [
        [
            "Pass rate",
            f"{market_passes}/{common_count} ({_fmt_float(100.0 * market_rate)}%)",
            f"{solo_passes}/{common_count} ({_fmt_float(100.0 * solo_rate)}%)",
        ],
        ["Total Input Tokens", _fmt_int(market.input_tokens), _fmt_int(solo.input_tokens)],
        ["Total Output Tokens", _fmt_int(market.output_tokens), _fmt_int(solo.output_tokens)],
        ["Total Calls", _fmt_int(market.total_calls), _fmt_int(solo.total_calls)],
    ]


def _render_table(
    *,
    col_labels: list[str],
    rows: list[list[str]],
    output_png: Path,
    output_svg: Path | None,
) -> None:
    n_rows = len(rows)
    fig_h = 0.8 + 0.72 * max(1, n_rows)
    fig, ax = plt.subplots(figsize=(8.0, fig_h))
    ax.axis("off")

    table = ax.table(
        cellText=rows,
        colLabels=col_labels,
        loc="center",
        cellLoc="left",
        colLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(13)
    table.scale(1.0, 1.18)

    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#d1d5db")
        if r == 0:
            cell.set_facecolor("#e5e7eb")
            cell.set_text_props(weight="bold")
        elif r % 2 == 1:
            cell.set_facecolor("#f9fafb")
        if c == 0:
            cell.set_text_props(weight="bold")

    fig.tight_layout(pad=0.15)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=250, bbox_inches="tight", pad_inches=0.02)
    if output_svg is not None:
        output_svg.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_svg, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render Market vs Solo GPT-5.2 overlap table from execution_token_usage.jsonl."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/research/data/phase2/execution_token_usage.jsonl"),
    )
    parser.add_argument(
        "--output-png",
        type=Path,
        default=Path("docs/research/report/data/phase2_market_vs_solo_overlap_table.png"),
    )
    parser.add_argument(
        "--output-svg",
        type=Path,
        default=Path("docs/research/report/data/phase2_market_vs_solo_overlap_table.svg"),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("docs/research/data/phase2/market_vs_solo_overlap_tokens.csv"),
    )
    parser.add_argument(
        "--market-vs-solo",
        type=Path,
        default=Path("docs/research/data/phase2/market_vs_solo_summary.json"),
    )
    parser.add_argument(
        "--copy-dir",
        type=Path,
        default=Path("/Users/fradkin/market_based_ai/slides/images"),
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"input not found: {args.input}")
    if not args.market_vs_solo.exists():
        raise SystemExit(f"market-vs-solo summary not found: {args.market_vs_solo}")

    rows = _read_jsonl(args.input)
    by_config: dict[str, dict[str, dict]] = {"market": {}, "solo_gpt52": {}}
    for row in rows:
        config = str(row.get("config") or "")
        if config not in by_config:
            continue
        task_raw = str(row.get("task_id") or "")
        task_key = _base_task_id(task_raw)
        by_config[config][task_key] = row

    overlap = sorted(set(by_config["market"]).intersection(set(by_config["solo_gpt52"])))
    if not overlap:
        raise SystemExit("no overlapping tasks found between market and solo_gpt52")

    market_rows = [by_config["market"][task_id] for task_id in overlap]
    solo_rows = [by_config["solo_gpt52"][task_id] for task_id in overlap]
    market_totals = _aggregate(market_rows)
    solo_totals = _aggregate(solo_rows)

    summary_payload = json.loads(args.market_vs_solo.read_text(encoding="utf-8"))
    market_passes = int((summary_payload.get("market") or {}).get("passes") or 0)
    solo_passes = int((summary_payload.get("solo_gpt52") or {}).get("passes") or 0)
    common_count = int(summary_payload.get("common_task_count") or 0)
    summary_rows = _build_table_rows(
        market=market_totals,
        solo=solo_totals,
        market_passes=market_passes,
        solo_passes=solo_passes,
        common_count=common_count,
    )
    _render_table(
        col_labels=["", "Market", "Solo 5.2"],
        rows=summary_rows,
        output_png=args.output_png,
        output_svg=args.output_svg,
    )

    # Also emit per-task detail CSV for auditability.
    csv_lines = [
        "task_id,market_total_tokens,solo_total_tokens,delta_tokens,market_calls,solo_calls,delta_calls"
    ]
    for task_id in overlap:
        market_row = by_config["market"][task_id]
        solo_row = by_config["solo_gpt52"][task_id]
        market_tokens = int(market_row.get("total_input_tokens") or 0) + int(
            market_row.get("total_output_tokens") or 0
        )
        solo_tokens = int(solo_row.get("total_input_tokens") or 0) + int(
            solo_row.get("total_output_tokens") or 0
        )
        market_calls = int(market_row.get("total_calls") or 0)
        solo_calls = int(solo_row.get("total_calls") or 0)
        csv_lines.append(
            ",".join(
                [
                    task_id,
                    str(market_tokens),
                    str(solo_tokens),
                    str(market_tokens - solo_tokens),
                    str(market_calls),
                    str(solo_calls),
                    str(market_calls - solo_calls),
                ]
            )
        )
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    copied_to = None
    if args.copy_dir.exists() and args.copy_dir.is_dir():
        copied_to = args.copy_dir / args.output_png.name
        shutil.copy2(args.output_png, copied_to)

    print(f"overlap_tasks={len(overlap)}")
    print(f"png={args.output_png}")
    print(f"svg={args.output_svg}")
    print(f"csv={args.output_csv}")
    if copied_to is not None:
        print(f"copied_to={copied_to}")
    else:
        print(f"copy_dir_missing={args.copy_dir}")


if __name__ == "__main__":
    main()
