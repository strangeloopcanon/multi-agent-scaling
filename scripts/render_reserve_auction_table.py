from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _pretty_model(model_ref: str) -> str:
    mapping = {
        "openai:gpt-5.2-2025-12-11": "GPT-5.2",
        "openai:gpt-5.2-pro-2025-12-11": "GPT-5.2-pro",
        "openai:gpt-5-mini-2025-08-07": "GPT-5-mini",
        "anthropic:claude-sonnet-4-5-20250929": "Claude Sonnet 4.5",
        "anthropic:claude-opus-4-5-20251101": "Claude Opus 4.5",
        "google:models/gemini-3-pro-preview": "Gemini 3 Pro Preview",
    }
    return mapping.get(model_ref, model_ref)


def _fmt_pct(v: float) -> str:
    return f"{100.0 * float(v):.1f}%"


def _fmt_money(v: float) -> str:
    return f"${float(v):.3f}"


def _fmt_num(v: float) -> str:
    return f"{float(v):.3f}"


def _latest_reserve_json() -> Path:
    root = Path("docs/research/data/phase1")
    matches = sorted(root.glob("reserve_auction_results_*.json"))
    if not matches:
        raise SystemExit(f"no reserve_auction_results_*.json found under {root}")
    return matches[-1]


def _load_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results") or {}
    by_model = results.get("by_model") or {}
    oracle_by_model = (results.get("perfect_knowledge_oracle") or {}).get("by_model") or {}
    rows: list[dict[str, Any]] = []
    for model_ref, stats in by_model.items():
        if model_ref == "google:models/gemini-3-pro-preview":
            continue
        oracle_stats = oracle_by_model.get(model_ref) or {}
        rows.append(
            {
                "model_ref": str(model_ref),
                "count": int(stats.get("count") or 0),
                "mean_win_rate": float(stats.get("mean_win_rate") or 0.0),
                "mean_expected_profit": float(stats.get("mean_expected_profit") or 0.0),
                "mean_realized_profit": float(stats.get("mean_realized_profit") or 0.0),
                "mean_oracle_profit": float(oracle_stats.get("mean_oracle_profit") or 0.0),
                "mean_breakeven_bid": float(stats.get("mean_breakeven_bid") or 0.0),
            }
        )
    rows.sort(key=lambda r: r["mean_expected_profit"], reverse=True)
    return rows, payload


def _render_svg(
    rows: list[dict[str, Any]], *, title: str, out_svg: Path, source: Path
) -> tuple[int, int]:
    headers = ["Model", "N", "Win %", "Exp Profit", "Realized", "Oracle Profit"]
    table_rows = [
        [
            _pretty_model(r["model_ref"]),
            str(r["count"]),
            _fmt_pct(r["mean_win_rate"]),
            _fmt_money(r["mean_expected_profit"]),
            _fmt_money(r["mean_realized_profit"]),
            _fmt_money(r["mean_oracle_profit"]),
        ]
        for r in rows
    ]

    x0 = 60
    y0 = 180
    row_h = 78
    col_w = [390, 90, 120, 170, 170, 170]
    table_w = sum(col_w)
    table_h = row_h * (1 + len(table_rows))
    width = x0 + table_w + 60
    height = max(760, y0 + table_h + 80)

    def esc(text: str) -> str:
        return html.escape(text, quote=True)

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
    )
    lines.append('<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>')
    lines.append(
        f'<text x="{x0}" y="74" font-family="Arial, Helvetica, sans-serif" font-size="46" '
        f'font-weight="700" fill="#111827">{esc(title)}</text>'
    )
    lines.append(
        f'<text x="{x0}" y="118" font-family="Arial, Helvetica, sans-serif" font-size="22" '
        f'fill="#374151">{esc("Model-level summary from reserve auction simulation.")}</text>'
    )

    lines.append(f'<rect x="{x0}" y="{y0}" width="{table_w}" height="{row_h}" fill="#f3f4f6"/>')
    for i in range(len(table_rows)):
        if i % 2 == 1:
            yy = y0 + row_h * (i + 1)
            lines.append(
                f'<rect x="{x0}" y="{yy}" width="{table_w}" height="{row_h}" fill="#f9fafb"/>'
            )

    lines.append('<g stroke="#d0d7de" stroke-width="2">')
    xx = x0
    for w in col_w:
        lines.append(f'<line x1="{xx}" y1="{y0}" x2="{xx}" y2="{y0 + table_h}"/>')
        xx += w
    lines.append(f'<line x1="{xx}" y1="{y0}" x2="{xx}" y2="{y0 + table_h}"/>')
    for r in range(len(table_rows) + 2):
        yy = y0 + row_h * r
        lines.append(f'<line x1="{x0}" y1="{yy}" x2="{x0 + table_w}" y2="{yy}"/>')
    lines.append("</g>")

    xx = x0
    for i, head in enumerate(headers):
        lines.append(
            f'<text x="{xx + 12}" y="{y0 + 50}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="23" font-weight="700" fill="#111827">{esc(head)}</text>'
        )
        xx += col_w[i]

    for ridx, row in enumerate(table_rows):
        y_text = y0 + row_h * (ridx + 1) + 50
        xx = x0
        for cidx, cell in enumerate(row):
            color = "#111827"
            weight = "400"
            if cidx == 5:  # oracle profit
                color = "#1d4ed8"
            lines.append(
                f'<text x="{xx + 12}" y="{y_text}" font-family="Arial, Helvetica, sans-serif" '
                f'font-size="23" font-weight="{weight}" fill="{color}">{esc(str(cell))}</text>'
            )
            xx += col_w[cidx]

    lines.append(
        f'<text x="{x0}" y="{y0 + table_h + 44}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="18" fill="#6b7280">{esc(f"Source: {source}")}</text>'
    )
    lines.append("</svg>")

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text("\n".join(lines), encoding="utf-8")
    return width, height


def _render_png(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise SystemExit("rsvg-convert not found. Install librsvg or run with --svg-only.")
    subprocess.run(
        [converter, "-w", str(width), "-h", str(height), str(svg_path), "-o", str(png_path)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render reserve auction model summary table.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to reserve_auction_results_*.json (defaults to latest in docs/research/data/phase1).",
    )
    parser.add_argument(
        "--output-png",
        type=Path,
        default=Path("docs/research/report/data/reserve_auction_model_summary.png"),
    )
    parser.add_argument(
        "--output-svg",
        type=Path,
        default=Path("docs/research/report/data/reserve_auction_model_summary.svg"),
    )
    parser.add_argument(
        "--title",
        default="Reserve Auction: Model Summary",
    )
    parser.add_argument("--svg-only", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input is not None else _latest_reserve_json()
    if not input_path.exists():
        raise SystemExit(f"input not found: {input_path}")

    rows, _payload = _load_rows(input_path)
    if not rows:
        raise SystemExit("no by_model rows found in input")

    width, height = _render_svg(
        rows,
        title=str(args.title),
        out_svg=Path(args.output_svg),
        source=input_path,
    )
    print(f"svg={Path(args.output_svg)}")

    if not args.svg_only:
        png_path = Path(args.output_png)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        _render_png(Path(args.output_svg), png_path, width, height)
        print(f"png={png_path}")

        slides_dir = Path.home() / "market_based_ai" / "slides" / "images"
        if slides_dir.exists() and slides_dir.is_dir():
            dest = slides_dir / png_path.name
            shutil.copy2(png_path, dest)
            print(f"copied_to={dest}")
        else:
            print(f"slides_dir_missing={slides_dir}")

    print(f"rows={len(rows)} input={input_path}")


if __name__ == "__main__":
    main()
