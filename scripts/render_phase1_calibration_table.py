from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

if __package__ in {None, ""}:
    # Allow `python scripts/render_phase1_calibration_table.py` from repo root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_economy.costing import default_price_for


@dataclass(frozen=True)
class ModelSummary:
    model_ref: str
    n: int
    mean_p_success: float
    pass_rate: float
    brier_score: float
    brier_skill: float
    mean_estimated_tokens: float
    mean_solve_tokens_cost_est: float
    median_est_over_cost_est_ratio: float


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _model_refs(records: list[dict[str, Any]]) -> list[str]:
    refs = sorted({str(r.get("model_ref") or "").strip() for r in records if r.get("model_ref")})
    return [r for r in refs if r]


def _default_blended_price_per_token(model_ref: str) -> float:
    # Equal-weight blend as a fallback when no explicit blended price is provided.
    p = default_price_for(model_ref)
    return ((float(p.input_per_1k) + float(p.output_per_1k)) / 2.0) / 1000.0


def _bootstrap_pricing_csv_if_missing(*, pricing_csv: Path, model_refs: list[str]) -> None:
    if pricing_csv.exists():
        return
    pricing_csv.parent.mkdir(parents=True, exist_ok=True)
    with pricing_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "model_ref",
                "input_per_1k",
                "output_per_1k",
                "blended_price_per_token",
                "source",
            ],
        )
        writer.writeheader()
        for model_ref in model_refs:
            price = default_price_for(model_ref)
            writer.writerow(
                {
                    "model_ref": model_ref,
                    "input_per_1k": f"{float(price.input_per_1k):.8f}",
                    "output_per_1k": f"{float(price.output_per_1k):.8f}",
                    "blended_price_per_token": f"{_default_blended_price_per_token(model_ref):.10f}",
                    "source": "agent_economy.costing.default_price_for",
                }
            )


def _load_pricing_csv(pricing_csv: Path) -> dict[str, float]:
    if not pricing_csv.exists():
        raise SystemExit(f"pricing csv not found: {pricing_csv}")
    out: dict[str, float] = {}
    with pricing_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"model_ref", "blended_price_per_token"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"pricing csv missing columns: {sorted(missing)} in {pricing_csv}")
        for row in reader:
            model_ref = str(row.get("model_ref") or "").strip()
            if not model_ref:
                continue
            blended = _as_float(row.get("blended_price_per_token"), default=0.0)
            if blended <= 0.0:
                continue
            out[model_ref] = blended
    return out


def _rows_with_outcomes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in records:
        outcome = row.get("outcome")
        if outcome is None:
            continue
        y = 1 if int(outcome) == 1 else 0
        p = max(0.0, min(1.0, _as_float(row.get("p_success"), default=0.0)))
        out.append({**row, "outcome": y, "p_success": p})
    return out


def _compute_summaries(
    rows: list[dict[str, Any]],
    *,
    blended_price_per_token_by_model: dict[str, float],
) -> list[ModelSummary]:
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        model_ref = str(row.get("model_ref") or "unknown")
        by_model.setdefault(model_ref, []).append(row)

    if not rows:
        return []

    base_rate = sum(int(r["outcome"]) for r in rows) / len(rows)
    base_brier = sum((base_rate - int(r["outcome"])) ** 2 for r in rows) / len(rows)

    summaries: list[ModelSummary] = []
    for model_ref, model_rows in by_model.items():
        n = len(model_rows)
        mean_p = sum(float(r["p_success"]) for r in model_rows) / n
        pass_rate = sum(int(r["outcome"]) for r in model_rows) / n
        brier = sum((float(r["p_success"]) - int(r["outcome"])) ** 2 for r in model_rows) / n
        brier_skill = 1.0 - (brier / base_brier) if base_brier > 0 else 0.0

        est_tokens = [
            int(r["estimated_tokens_total"])
            for r in model_rows
            if r.get("estimated_tokens_total") is not None
        ]
        blended = blended_price_per_token_by_model.get(model_ref)
        if blended is None or blended <= 0.0:
            blended = _default_blended_price_per_token(model_ref)
        solve_token_cost_estimates = []
        for r in model_rows:
            cost = float(_as_float(r.get("external_cost"), default=0.0))
            if cost <= 0.0:
                continue
            solve_token_cost_estimates.append(cost / blended)
        ratios = [
            (float(e) / float(a_est))
            for e, a_est in zip(est_tokens, solve_token_cost_estimates, strict=False)
            if a_est > 0
        ]

        summaries.append(
            ModelSummary(
                model_ref=model_ref,
                n=n,
                mean_p_success=mean_p,
                pass_rate=pass_rate,
                brier_score=brier,
                brier_skill=brier_skill,
                mean_estimated_tokens=(sum(est_tokens) / len(est_tokens)) if est_tokens else 0.0,
                mean_solve_tokens_cost_est=(
                    sum(solve_token_cost_estimates) / len(solve_token_cost_estimates)
                )
                if solve_token_cost_estimates
                else 0.0,
                median_est_over_cost_est_ratio=median(ratios) if ratios else 0.0,
            )
        )

    summaries.sort(key=lambda s: (s.pass_rate, s.brier_skill), reverse=True)
    return summaries


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
    return f"{v * 100:.1f}%"


def _fmt_float(v: float) -> str:
    return f"{v:.3f}"


def _fmt_intish(v: float) -> str:
    return f"{int(round(v)):,}"


def _render_svg(
    *,
    summaries: list[ModelSummary],
    source_path: Path,
    out_svg: Path,
    title: str,
    pricing_csv_path: Path,
) -> tuple[int, int]:
    headers = [
        "Model",
        "N",
        "Mean p",
        "Pass %",
        "Brier",
        "Skill",
        "Est toks",
        "$-implied toks",
        "Est/$",
    ]
    rows = [
        [
            _pretty_model(s.model_ref),
            str(s.n),
            _fmt_pct(s.mean_p_success),
            _fmt_pct(s.pass_rate),
            _fmt_float(s.brier_score),
            f"{s.brier_skill:+.3f}",
            _fmt_intish(s.mean_estimated_tokens),
            _fmt_intish(s.mean_solve_tokens_cost_est),
            _fmt_float(s.median_est_over_cost_est_ratio),
        ]
        for s in summaries
    ]

    x0 = 60
    y0 = 200
    row_h = 82
    col_w = [360, 85, 195, 155, 150, 140, 205, 220, 210]
    table_w = sum(col_w)
    table_h = row_h * (1 + len(rows))
    width = x0 + table_w + 60
    height = max(820, y0 + table_h + 190)

    def esc(text: str) -> str:
        return html.escape(text, quote=True)

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
    )
    lines.append('<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>')
    lines.append(
        f'<text x="{x0}" y="78" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="48" font-weight="700" fill="#111827">{esc(title)}</text>'
    )
    lines.append(
        f'<text x="{x0}" y="125" font-family="Arial, Helvetica, sans-serif" '
        'font-size="24" fill="#374151">'
        "Calibration metrics from JSONL outcomes and token fields."
        "</text>"
    )

    lines.append(f'<rect x="{x0}" y="{y0}" width="{table_w}" height="{row_h}" fill="#f3f4f6"/>')
    for i in range(len(rows)):
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
    for r in range(len(rows) + 2):
        yy = y0 + row_h * r
        lines.append(f'<line x1="{x0}" y1="{yy}" x2="{x0 + table_w}" y2="{yy}"/>')
    lines.append("</g>")

    xx = x0
    for i, head in enumerate(headers):
        lines.append(
            f'<text x="{xx + 12}" y="{y0 + 52}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="24" font-weight="700" fill="#111827">{esc(head)}</text>'
        )
        xx += col_w[i]

    for ridx, row in enumerate(rows):
        y_text = y0 + row_h * (ridx + 1) + 52
        xx = x0
        for cidx, cell in enumerate(row):
            fill = "#111827"
            weight = "400"
            if cidx == 5:
                if str(cell).startswith("+"):
                    fill = "#065f46"
                    weight = "700"
                else:
                    fill = "#7f1d1d"
            lines.append(
                f'<text x="{xx + 12}" y="{y_text}" font-family="Arial, Helvetica, sans-serif" '
                f'font-size="24" font-weight="{weight}" fill="{fill}">{esc(str(cell))}</text>'
            )
            xx += col_w[cidx]

    lines.append("</svg>")

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text("\n".join(lines), encoding="utf-8")
    return width, height


def _render_png_from_svg(
    *,
    svg_path: Path,
    png_path: Path,
    width: int,
    height: int,
) -> None:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        raise SystemExit(
            "rsvg-convert not found. Install librsvg (e.g. `brew install librsvg`) "
            "or run with --svg-only."
        )
    subprocess.run(
        [converter, "-w", str(width), "-h", str(height), str(svg_path), "-o", str(png_path)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a presentation-ready calibration table PNG from Phase I calibration_results.jsonl."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/research/data/phase1/calibration_results.jsonl"),
        help="Path to calibration_results.jsonl",
    )
    parser.add_argument(
        "--output-png",
        type=Path,
        default=Path("docs/research/report/data/phase1_calibration_table.png"),
        help="Output PNG path",
    )
    parser.add_argument(
        "--output-svg",
        type=Path,
        default=Path("docs/research/report/data/phase1_calibration_table.svg"),
        help="Output SVG path",
    )
    parser.add_argument(
        "--title",
        default="Phase I Calibration: Probability Signal and Token Estimates",
        help="Table title",
    )
    parser.add_argument(
        "--pricing-csv",
        type=Path,
        default=Path("docs/research/data/phase1/model_token_pricing.csv"),
        help="CSV with per-model blended_price_per_token.",
    )
    parser.add_argument(
        "--svg-only",
        action="store_true",
        help="Only write SVG (skip PNG conversion)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"input file not found: {input_path}")

    records = _load_jsonl(input_path)
    refs = _model_refs(records)
    _bootstrap_pricing_csv_if_missing(pricing_csv=Path(args.pricing_csv), model_refs=refs)
    blended_price_per_token_by_model = _load_pricing_csv(Path(args.pricing_csv))
    rows = _rows_with_outcomes(records)
    if not rows:
        raise SystemExit("no rows with outcome found in input file")

    summaries = _compute_summaries(
        rows, blended_price_per_token_by_model=blended_price_per_token_by_model
    )
    width, height = _render_svg(
        summaries=summaries,
        source_path=input_path,
        out_svg=Path(args.output_svg),
        title=str(args.title),
        pricing_csv_path=Path(args.pricing_csv),
    )

    if not args.svg_only:
        output_png = Path(args.output_png)
        output_png.parent.mkdir(parents=True, exist_ok=True)
        _render_png_from_svg(
            svg_path=Path(args.output_svg),
            png_path=output_png,
            width=width,
            height=height,
        )
        print(f"png={output_png}")

        slides_dir = Path.home() / "market_based_ai" / "slides" / "images"
        if slides_dir.exists() and slides_dir.is_dir():
            slides_target = slides_dir / output_png.name
            try:
                shutil.copy2(output_png, slides_target)
                print(f"copied_to={slides_target}")
            except PermissionError as e:
                print(f"copy_failed_permission={slides_target} error={e}")
            except OSError as e:
                print(f"copy_failed_oserror={slides_target} error={e}")
        else:
            print(f"slides_dir_missing={slides_dir}")

    print(f"svg={Path(args.output_svg)}")
    print(f"models={len(summaries)} rows={len(rows)}")


if __name__ == "__main__":
    main()
