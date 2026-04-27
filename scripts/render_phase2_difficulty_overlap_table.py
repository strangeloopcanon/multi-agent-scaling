from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt


@dataclass(frozen=True)
class BandStats:
    name: str
    n: int
    external_gpt52_passes: int
    market_tokens: int
    solo_tokens: int
    market_only: int
    solo_only: int

    @property
    def external_gpt52_rate(self) -> float:
        return (self.external_gpt52_passes / self.n) if self.n > 0 else 0.0

    @property
    def market_tokens_per_task(self) -> float:
        return (self.market_tokens / self.n) if self.n > 0 else 0.0

    @property
    def solo_tokens_per_task(self) -> float:
        return (self.solo_tokens / self.n) if self.n > 0 else 0.0


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _base_task_id(raw: str) -> str:
    value = str(raw)
    if "_" not in value:
        return value
    head, tail = value.split("_", 1)
    if head.isdigit():
        return tail
    return value


def _fmt_int(v: int) -> str:
    return f"{int(v):,}"


def _fmt_pct(v: float) -> str:
    return f"{100.0 * float(v):.0f}%"


def _fmt_k(v: float) -> str:
    return f"{float(v) / 1000.0:.1f}k"


def _load_overlap_token_rows(path: Path) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    market_rows: dict[str, dict] = {}
    solo_rows: dict[str, dict] = {}
    for row in _read_jsonl(path):
        cfg = str(row.get("config") or "")
        task = _base_task_id(str(row.get("task_id") or ""))
        if not task:
            continue
        if cfg == "market":
            market_rows[task] = row
        elif cfg == "solo_gpt52":
            solo_rows[task] = row
    overlap = sorted(set(market_rows).intersection(set(solo_rows)))
    return market_rows, solo_rows, overlap


def _load_external_rates_and_gpt52(
    path: Path, overlap: list[str]
) -> tuple[dict[str, float], dict[str, int]]:
    overlap_set = set(overlap)
    outcomes_by_task: dict[str, list[int]] = defaultdict(list)
    gpt52_outcome: dict[str, int] = {}
    for row in _read_jsonl(path):
        if str(row.get("benchmark") or "") != "swebench":
            continue
        if str(row.get("strategy") or "") != "direct":
            continue
        task = str(row.get("task_id") or "")
        if task not in overlap_set:
            continue
        outcome = int(row.get("outcome") or 0)
        outcomes_by_task[task].append(outcome)
        if str(row.get("model_ref") or "") == "openai:gpt-5.2-2025-12-11":
            gpt52_outcome[task] = outcome

    external_rate = {
        task: (sum(vals) / len(vals) if vals else 0.0) for task, vals in outcomes_by_task.items()
    }
    return external_rate, gpt52_outcome


def _band_name(rate: float) -> str:
    if rate <= 0.5:
        return "Hard (<=0.5)"
    if abs(rate - 1.0) < 1e-12:
        return "Very Easy (=1.0)"
    return "Middle"


def _render(
    *,
    rows: list[list[str]],
    title: str,
    subtitle: str,
    output_png: Path,
    output_svg: Path | None,
) -> None:
    fig, ax = plt.subplots(figsize=(13.5, 5.8))
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#0f172a")
    ax.axis("off")

    col_labels = [
        "Band",
        "n",
        "Market pass rate",
        "External GPT-5.2",
        "Market tokens/task",
        "Solo tokens/task",
        "Market-only",
        "Solo-only",
    ]
    table = ax.table(
        cellText=rows, colLabels=col_labels, cellLoc="left", colLoc="left", loc="center"
    )
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.0, 1.6)

    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#334155")
        if r == 0:
            cell.set_facecolor("#1e293b")
            cell.get_text().set_color("#f8fafc")
            cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor("#111827" if r % 2 else "#0b1220")
            cell.get_text().set_color("#e5e7eb")
        if c == 0 and r > 0:
            cell.get_text().set_weight("bold")

    fig.text(
        0.012, 0.965, title, color="#f8fafc", fontsize=18, fontweight="bold", ha="left", va="top"
    )
    fig.text(0.012, 0.918, subtitle, color="#94a3b8", fontsize=11, ha="left", va="top")

    fig.tight_layout(rect=[0, 0, 1, 0.88])
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=220, facecolor=fig.get_facecolor())
    if output_svg is not None:
        output_svg.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_svg, facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render difficulty-stratified Market vs Solo overlap view from JSON files."
    )
    parser.add_argument(
        "--token-usage",
        type=Path,
        default=Path("docs/research/data/phase2/execution_token_usage.jsonl"),
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path("docs/research/data/phase1/calibration_results.jsonl"),
    )
    parser.add_argument(
        "--market-vs-solo",
        type=Path,
        default=Path("docs/research/data/phase2/market_vs_solo_summary.json"),
    )
    parser.add_argument(
        "--output-png",
        type=Path,
        default=Path("docs/research/report/data/phase2_difficulty_overlap_table.png"),
    )
    parser.add_argument(
        "--output-svg",
        type=Path,
        default=Path("docs/research/report/data/phase2_difficulty_overlap_table.svg"),
    )
    parser.add_argument(
        "--copy-dir",
        type=Path,
        default=None,
        help="Optional directory to copy the rendered PNG into.",
    )
    args = parser.parse_args()

    if not args.token_usage.exists():
        raise SystemExit(f"missing token usage file: {args.token_usage}")
    if not args.calibration.exists():
        raise SystemExit(f"missing calibration file: {args.calibration}")
    if not args.market_vs_solo.exists():
        raise SystemExit(f"missing market-vs-solo file: {args.market_vs_solo}")

    market_rows, solo_rows, overlap = _load_overlap_token_rows(args.token_usage)
    if not overlap:
        raise SystemExit("no overlap between market and solo_gpt52 in token usage file")

    external_rate, gpt52_outcome = _load_external_rates_and_gpt52(args.calibration, overlap)
    summary = json.loads(args.market_vs_solo.read_text(encoding="utf-8"))
    market_only_ids = set(str(x) for x in summary.get("market_only_task_ids", []))
    solo_only_ids = set(str(x) for x in summary.get("solo_only_task_ids", []))

    grouped: dict[str, list[str]] = defaultdict(list)
    for task in overlap:
        grouped[_band_name(float(external_rate.get(task, 0.0)))].append(task)

    order = ["Hard (<=0.5)", "Very Easy (=1.0)", "Middle"]
    band_stats: list[BandStats] = []
    for band in order:
        tasks = sorted(grouped.get(band, []))
        if not tasks:
            continue
        m_tokens = 0
        s_tokens = 0
        ext_pass = 0
        market_only = 0
        solo_only = 0
        for task in tasks:
            m = market_rows[task]
            s = solo_rows[task]
            m_tokens += int(m.get("total_input_tokens") or 0) + int(
                m.get("total_output_tokens") or 0
            )
            s_tokens += int(s.get("total_input_tokens") or 0) + int(
                s.get("total_output_tokens") or 0
            )
            ext_pass += int(gpt52_outcome.get(task, 0))
            market_only += 1 if task in market_only_ids else 0
            solo_only += 1 if task in solo_only_ids else 0
        band_stats.append(
            BandStats(
                name=band,
                n=len(tasks),
                external_gpt52_passes=ext_pass,
                market_tokens=m_tokens,
                solo_tokens=s_tokens,
                market_only=market_only,
                solo_only=solo_only,
            )
        )

    total_market_tokens = sum(
        int(market_rows[t].get("total_input_tokens") or 0)
        + int(market_rows[t].get("total_output_tokens") or 0)
        for t in overlap
    )
    total_solo_tokens = sum(
        int(solo_rows[t].get("total_input_tokens") or 0)
        + int(solo_rows[t].get("total_output_tokens") or 0)
        for t in overlap
    )
    market_summary = summary.get("market", {}) if isinstance(summary, dict) else {}
    common_n = int(summary.get("common_task_count") or 0) if isinstance(summary, dict) else 0
    market_passes = (
        int(market_summary.get("passes") or 0) if isinstance(market_summary, dict) else 0
    )
    market_rate = (
        float(market_summary.get("rate") or 0.0) if isinstance(market_summary, dict) else 0.0
    )
    market_pass_label = (
        f"{_fmt_int(market_passes)}/{_fmt_int(common_n)} ({_fmt_pct(market_rate)})"
        if common_n > 0
        else "n/a"
    )

    rows = []
    for b in band_stats:
        rows.append(
            [
                b.name,
                _fmt_int(b.n),
                market_pass_label,
                f"{_fmt_int(b.external_gpt52_passes)}/{_fmt_int(b.n)} ({_fmt_pct(b.external_gpt52_rate)})",
                _fmt_k(b.market_tokens_per_task),
                _fmt_k(b.solo_tokens_per_task),
                _fmt_int(b.market_only),
                _fmt_int(b.solo_only),
            ]
        )

    rows.append(
        [
            "ALL overlap",
            _fmt_int(len(overlap)),
            market_pass_label,
            "n/a",
            _fmt_k(total_market_tokens / max(1, len(overlap))),
            _fmt_k(total_solo_tokens / max(1, len(overlap))),
            _fmt_int(sum(1 for t in overlap if t in market_only_ids)),
            _fmt_int(sum(1 for t in overlap if t in solo_only_ids)),
        ]
    )

    subtitle = (
        "Bands from external_success_rate in phase1 calibration; "
        "token efficiency from phase2 execution usage; "
        "market-only/solo-only are paired disagreement counts."
    )
    _render(
        rows=rows,
        title="Difficulty-Stratified Signal (Market vs Solo, Overlap Tasks)",
        subtitle=subtitle,
        output_png=args.output_png,
        output_svg=args.output_svg,
    )

    copied_to = None
    if args.copy_dir is not None and args.copy_dir.exists() and args.copy_dir.is_dir():
        copied_to = args.copy_dir / args.output_png.name
        shutil.copy2(args.output_png, copied_to)

    print(f"overlap_tasks={len(overlap)}")
    print(f"bands={','.join(x.name for x in band_stats)}")
    print(f"png={args.output_png}")
    print(f"svg={args.output_svg}")
    if copied_to is not None:
        print(f"copied_to={copied_to}")
    elif args.copy_dir is not None:
        print(f"copy_dir_missing={args.copy_dir}")


if __name__ == "__main__":
    main()
