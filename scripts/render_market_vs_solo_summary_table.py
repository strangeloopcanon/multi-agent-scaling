from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


def _fmt_pct(value: float) -> str:
    return f"{100.0 * float(value):.1f}%"


def _render_overview(ax: plt.Axes, payload: dict) -> None:
    ax.axis("off")
    common = int(payload.get("common_task_count") or 0)
    market = payload.get("market") or {}
    solo = payload.get("solo_gpt52") or {}
    confusion = payload.get("confusion") or {}
    p_val = float(payload.get("mcnemar_exact_p_two_sided") or 0.0)

    rows = [
        ["Common tasks", f"{common}"],
        ["Market passes", f"{int(market.get('passes') or 0)}/{common} ({_fmt_pct(market.get('rate') or 0.0)})"],
        ["Solo GPT-5.2 passes", f"{int(solo.get('passes') or 0)}/{common} ({_fmt_pct(solo.get('rate') or 0.0)})"],
        ["Delta (market - solo)", f"{int(payload.get('delta_market_minus_solo') or 0):+d}"],
        [
            "Confusion (both_pass / market_only / solo_only / both_fail)",
            f"{int(confusion.get('both_pass') or 0)} / {int(confusion.get('market_only') or 0)} / "
            f"{int(confusion.get('solo_only') or 0)} / {int(confusion.get('both_fail') or 0)}",
        ],
        ["McNemar exact p (two-sided)", f"{p_val:.6f}"],
    ]
    table = ax.table(
        cellText=rows,
        colLabels=["Metric", "Value"],
        cellLoc="left",
        colLoc="left",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.4)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#d1d5db")
        if r == 0:
            cell.set_facecolor("#e5e7eb")
            cell.get_text().set_weight("bold")
        elif r % 2 == 1:
            cell.set_facecolor("#f9fafb")
        if c == 0 and r > 0:
            cell.get_text().set_weight("bold")


def _render_by_repo(ax: plt.Axes, payload: dict) -> None:
    ax.axis("off")
    by_repo = payload.get("by_repo") or {}
    repos = sorted(by_repo.keys())
    rows = []
    for repo in repos:
        r = by_repo[repo] or {}
        rows.append(
            [
                repo,
                str(int(r.get("n") or 0)),
                f"{int(r.get('market_pass') or 0)}/{int(r.get('n') or 0)} ({_fmt_pct(r.get('market_rate') or 0.0)})",
                f"{int(r.get('solo_pass') or 0)}/{int(r.get('n') or 0)} ({_fmt_pct(r.get('solo_rate') or 0.0)})",
                str(int(r.get("market_only") or 0)),
                str(int(r.get("solo_only") or 0)),
                f"{int(r.get('delta_market_minus_solo') or 0):+d}",
            ]
        )

    table = ax.table(
        cellText=rows,
        colLabels=["Repo", "N", "Market", "Solo", "M-only", "S-only", "Delta"],
        cellLoc="left",
        colLoc="left",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.25)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#d1d5db")
        if r == 0:
            cell.set_facecolor("#e5e7eb")
            cell.get_text().set_weight("bold")
        elif r % 2 == 1:
            cell.set_facecolor("#f9fafb")
        if c == 0 and r > 0:
            cell.get_text().set_weight("bold")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render table summary from market_vs_solo_summary.json"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/research/data/phase2/market_vs_solo_summary.json"),
    )
    parser.add_argument(
        "--output-png",
        type=Path,
        default=Path("docs/research/report/data/market_vs_solo_summary_table.png"),
    )
    parser.add_argument(
        "--output-svg",
        type=Path,
        default=Path("docs/research/report/data/market_vs_solo_summary_table.svg"),
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"input not found: {args.input}")
    payload = json.loads(args.input.read_text(encoding="utf-8"))

    fig = plt.figure(figsize=(15, 10))
    gs = fig.add_gridspec(2, 1, height_ratios=[1, 2], hspace=0.22)
    ax_top = fig.add_subplot(gs[0, 0])
    ax_bottom = fig.add_subplot(gs[1, 0])

    fig.suptitle("Market vs Solo GPT-5.2 Summary", fontsize=18, fontweight="bold", y=0.98)
    fig.text(
        0.01,
        0.955,
        f"Source: {args.input}",
        fontsize=10,
        color="#374151",
        ha="left",
        va="top",
    )

    _render_overview(ax_top, payload)
    _render_by_repo(ax_bottom, payload)

    args.output_png.parent.mkdir(parents=True, exist_ok=True)
    args.output_svg.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output_png, dpi=220, bbox_inches="tight")
    fig.savefig(args.output_svg, bbox_inches="tight")
    plt.close(fig)

    print(f"png={args.output_png}")
    print(f"svg={args.output_svg}")


if __name__ == "__main__":
    main()
