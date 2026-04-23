from __future__ import annotations

import argparse
import json
import math
import random
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BOOTSTRAP_SAMPLES = 6000
BOOTSTRAP_SEED = 20260420

WHITE = "#ffffff"
INK = "#111827"
MUTED = "#4b5563"
GRID = "#d7dde3"
SECTION = "#6b7280"


@dataclass(frozen=True)
class CalibrationEntry:
    label: str
    definition: str
    mean: float
    ci_low: float
    ci_high: float
    color: str


@dataclass(frozen=True)
class LiveEntry:
    label: str
    definition: str
    passes: int
    total: int
    group: str
    color: str

    @property
    def rate(self) -> float:
        return self.passes / self.total


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _calibration_brier_by_key(path: Path) -> dict[tuple[str, str], float]:
    pairs: dict[tuple[str, str], float] = {}
    for row in _load_jsonl(path):
        if row.get("outcome") is None:
            continue
        task_id = str(row.get("task_id") or "").strip()
        model_ref = str(row.get("model_ref") or "").strip()
        if not task_id or not model_ref:
            continue
        outcome = 1 if int(row["outcome"]) == 1 else 0
        p_success = float(row.get("p_success") or 0.0)
        p_success = max(0.0, min(1.0, p_success))
        pairs[(task_id, model_ref)] = (p_success - outcome) ** 2
    return pairs


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("cannot take percentile of empty list")
    index = int(round((len(sorted_values) - 1) * q))
    index = max(0, min(index, len(sorted_values) - 1))
    return sorted_values[index]


def _paired_bootstrap_mean_ci(
    baseline: dict[tuple[str, str], float],
    experiment: dict[tuple[str, str], float],
) -> tuple[CalibrationEntry, CalibrationEntry, tuple[float, float, float]]:
    keys = sorted(set(baseline) & set(experiment))
    if not keys:
        raise SystemExit("no overlapping calibration rows between baseline and experiment")

    baseline_values = [baseline[key] for key in keys]
    experiment_values = [experiment[key] for key in keys]
    n = len(keys)

    baseline_mean = sum(baseline_values) / n
    experiment_mean = sum(experiment_values) / n
    delta_mean = experiment_mean - baseline_mean

    rng = random.Random(BOOTSTRAP_SEED)
    baseline_means: list[float] = []
    experiment_means: list[float] = []
    delta_means: list[float] = []

    for _ in range(BOOTSTRAP_SAMPLES):
        baseline_total = 0.0
        experiment_total = 0.0
        for _ in range(n):
            idx = rng.randrange(n)
            baseline_total += baseline_values[idx]
            experiment_total += experiment_values[idx]
        baseline_sample_mean = baseline_total / n
        experiment_sample_mean = experiment_total / n
        baseline_means.append(baseline_sample_mean)
        experiment_means.append(experiment_sample_mean)
        delta_means.append(experiment_sample_mean - baseline_sample_mean)

    baseline_means.sort()
    experiment_means.sort()
    delta_means.sort()

    baseline_entry = CalibrationEntry(
        label="Phase I direct calibration",
        definition="Direct self-forecasting on the 93-task, six-model calibration set.",
        mean=baseline_mean,
        ci_low=_percentile(baseline_means, 0.025),
        ci_high=_percentile(baseline_means, 0.975),
        color="#68727c",
    )
    experiment_entry = CalibrationEntry(
        label="Phase Ib self-knowledge card",
        definition="Same task-model pairs after adding a held-out self-history card before forecasting.",
        mean=experiment_mean,
        ci_low=_percentile(experiment_means, 0.025),
        ci_high=_percentile(experiment_means, 0.975),
        color="#1f4f78",
    )
    delta = (
        delta_mean,
        _percentile(delta_means, 0.025),
        _percentile(delta_means, 0.975),
    )
    return baseline_entry, experiment_entry, delta


def _wilson_interval(passes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("total must be positive")
    p = passes / total
    denominator = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denominator
    margin = (
        z * math.sqrt((p * (1.0 - p) / total) + ((z * z) / (4.0 * total * total))) / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _extract_fraction(text: str, pattern: str, *, description: str) -> tuple[int, int]:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"could not find {description!r} in source text")
    return int(match.group(1)), int(match.group(2))


def _load_live_entries(
    *,
    phase2_rollup_path: Path,
    market_vs_solo_path: Path,
    codex_summary_path: Path,
    phase2_report_path: Path,
) -> list[LiveEntry]:
    phase2_rollup = json.loads(phase2_rollup_path.read_text(encoding="utf-8"))
    market_vs_solo = json.loads(market_vs_solo_path.read_text(encoding="utf-8"))
    codex_summary = json.loads(codex_summary_path.read_text(encoding="utf-8"))
    phase2_report = phase2_report_path.read_text(encoding="utf-8")

    oracle_passes = int(phase2_rollup["external_oracle"]["passes"])
    oracle_total = int(phase2_rollup["task_count"])
    external_gpt52_passes = int(
        phase2_rollup["external_by_model"]["openai:gpt-5.2-2025-12-11"]["passes"]
    )
    external_gpt52_total = int(
        phase2_rollup["external_by_model"]["openai:gpt-5.2-2025-12-11"]["tasks"]
    )
    diagnostic_passes = int(codex_summary["results"]["passes"])
    diagnostic_total = int(codex_summary["setup"]["task_count"])

    published_market_passes = int(market_vs_solo["market"]["passes"])
    published_market_total = int(market_vs_solo["common_task_count"])
    published_solo_passes = int(market_vs_solo["solo_gpt52"]["passes"])
    published_solo_total = int(market_vs_solo["common_task_count"])

    matched_central_passes, matched_central_total = _extract_fraction(
        phase2_report,
        r"centralized router solved `(\d+)\s*/\s*(\d+)` tasks",
        description="matched centralized router result",
    )
    matched_market_passes, matched_market_total = _extract_fraction(
        phase2_report,
        r"market solved `(\d+)\s*/\s*(\d+)` tasks",
        description="matched market rerun result",
    )
    hard_prior_passes, hard_prior_total = _extract_fraction(
        phase2_report,
        r"The Phase IId result used in the repo and paper is `(\d+)\s*/\s*(\d+)`\.",
        description="hard-prior market result",
    )

    return [
        LiveEntry(
            label="Best external model per task",
            definition="Upper bound formed by taking the best externally scaffolded single-model result on each task in hindsight.",
            passes=oracle_passes,
            total=oracle_total,
            group="Reference runs",
            color="#1f2933",
        ),
        LiveEntry(
            label="External GPT-5.2 run",
            definition="Single GPT-5.2 run on the standard external SWE-bench scaffold.",
            passes=external_gpt52_passes,
            total=external_gpt52_total,
            group="Reference runs",
            color="#364152",
        ),
        LiveEntry(
            label="30-minute Codex diagnostic",
            definition="Single-model Codex-path diagnostic with a 30-minute per-task budget.",
            passes=diagnostic_passes,
            total=diagnostic_total,
            group="Reference runs",
            color="#425466",
        ),
        LiveEntry(
            label="Original six-model market run",
            definition="Original Phase II six-model market run with a 15-minute per-task budget.",
            passes=published_market_passes,
            total=published_market_total,
            group="Original 15-minute scaffold",
            color="#67717d",
        ),
        LiveEntry(
            label="Original solo GPT-5.2 run",
            definition="Original Phase II single-model GPT-5.2 run with that same 15-minute budget.",
            passes=published_solo_passes,
            total=published_solo_total,
            group="Original 15-minute scaffold",
            color="#8a949f",
        ),
        LiveEntry(
            label="Market with calibration prior",
            definition="Matched six-model rerun with the same 15-minute budget, but bids start from a held-out calibration prior.",
            passes=hard_prior_passes,
            total=hard_prior_total,
            group="Matched 15-minute rerun",
            color="#1f4f78",
        ),
        LiveEntry(
            label="Central router on matched rerun",
            definition="Matched six-model rerun with the same 15-minute budget and a centralized chooser.",
            passes=matched_central_passes,
            total=matched_central_total,
            group="Matched 15-minute rerun",
            color="#3a6076",
        ),
        LiveEntry(
            label="Original market rule on matched rerun",
            definition="Matched six-model rerun with the same 15-minute budget and the original market-clearing rule.",
            passes=matched_market_passes,
            total=matched_market_total,
            group="Matched 15-minute rerun",
            color="#5f7f93",
        ),
    ]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
                "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            ]
        )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _measure_multiline(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, *, spacing: int
) -> tuple[int, int]:
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: str,
    anchor: str = "la",
    spacing: int = 4,
) -> tuple[int, int]:
    draw.multiline_text(xy, text, font=font, fill=fill, anchor=anchor, spacing=spacing)
    return _measure_multiline(draw, text, font, spacing=spacing)


def _draw_horizontal_axis(
    draw: ImageDraw.ImageDraw,
    *,
    x0: int,
    x1: int,
    y0: int,
    tick_values: list[float],
    value_to_x,
    tick_font: ImageFont.ImageFont,
    tick_formatter,
    show_grid_top: int,
    show_grid_bottom: int,
) -> None:
    draw.line((x0, y0, x1, y0), fill=GRID, width=2)
    for value in tick_values:
        x = value_to_x(value)
        draw.line((x, show_grid_top, x, show_grid_bottom), fill=GRID, width=2)
        draw.line((x, y0, x, y0 + 10), fill=GRID, width=2)
        label = tick_formatter(value)
        bbox = draw.textbbox((0, 0), label, font=tick_font)
        draw.text((x - (bbox[2] - bbox[0]) / 2, y0 + 18), label, font=tick_font, fill=MUTED)


def _render_calibration(
    *,
    entries: list[CalibrationEntry],
    delta: tuple[float, float, float],
    output_png: Path,
) -> None:
    width = 1800
    height = 600
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(34, bold=True)
    body_bold = _load_font(28, bold=True)
    body_font = _load_font(24)
    small_font = _load_font(22)
    tick_font = _load_font(20)

    left = 70
    label_right = 640
    plot_left = 720
    plot_right = 1460
    value_left = 1510
    top = 74
    row_y = [230, 380]

    x_min = min(entry.ci_low for entry in entries) - 0.010
    x_max = max(entry.ci_high for entry in entries) + 0.018

    def value_to_x(value: float) -> int:
        span = x_max - x_min
        return int(plot_left + ((value - x_min) / span) * (plot_right - plot_left))

    draw.text((left, top), "Calibration", font=title_font, fill=INK)
    meta_label = "95% paired bootstrap CI"
    meta_bbox = draw.textbbox((0, 0), meta_label, font=small_font)
    draw.text(
        (width - 70 - (meta_bbox[2] - meta_bbox[0]), top + 8),
        meta_label,
        font=small_font,
        fill=MUTED,
    )

    _draw_horizontal_axis(
        draw,
        x0=plot_left,
        x1=plot_right,
        y0=500,
        tick_values=[0.155, 0.165, 0.175, 0.185, 0.195],
        value_to_x=value_to_x,
        tick_font=tick_font,
        tick_formatter=lambda value: f"{value:.3f}",
        show_grid_top=165,
        show_grid_bottom=490,
    )
    axis_label = "Mean Brier score on 558 model-task forecasts (lower is better)"
    axis_bbox = draw.textbbox((0, 0), axis_label, font=small_font)
    draw.text(
        ((plot_left + plot_right - (axis_bbox[2] - axis_bbox[0])) / 2, 548),
        axis_label,
        font=small_font,
        fill=MUTED,
    )

    for entry, y in zip(entries, row_y):
        draw.line(
            (value_to_x(entry.ci_low), y, value_to_x(entry.ci_high), y), fill=entry.color, width=6
        )
        dot_x = value_to_x(entry.mean)
        draw.ellipse((dot_x - 10, y - 10, dot_x + 10, y + 10), fill=entry.color)

        title_y = y - 32
        draw.text((label_right, title_y), entry.label, font=body_bold, fill=INK, anchor="ra")
        wrapped = textwrap.fill(entry.definition, width=55)
        _, body_h = _draw_text(
            draw,
            (label_right, y + 8),
            wrapped,
            font=body_font,
            fill=MUTED,
            anchor="ra",
            spacing=6,
        )
        value_label = f"{entry.mean:.4f}"
        draw.text((value_left, y - 10), value_label, font=body_bold, fill=INK)

    delta_mean, delta_low, delta_high = delta
    delta_text = (
        f"Self-knowledge delta: {delta_mean:+.4f} (95% CI {delta_low:+.4f} to {delta_high:+.4f})"
    )
    draw.text((left, 560), delta_text, font=small_font, fill=MUTED)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_png)


def _render_live_performance(
    *,
    entries: list[LiveEntry],
    output_png: Path,
) -> None:
    width = 2200
    height = 1260
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(34, bold=True)
    section_font = _load_font(24, bold=True)
    body_bold = _load_font(28, bold=True)
    body_font = _load_font(23)
    small_font = _load_font(22)
    tick_font = _load_font(20)

    left = 70
    label_right = 1030
    plot_left = 1100
    plot_right = 1910
    value_left = 1970
    top = 64
    axis_y = 1070

    group_order = ["Reference runs", "Original 15-minute scaffold", "Matched 15-minute rerun"]
    group_to_entries: dict[str, list[LiveEntry]] = {group: [] for group in group_order}
    for entry in entries:
        group_to_entries.setdefault(entry.group, []).append(entry)

    rows: list[tuple[LiveEntry, int]] = []
    headers: list[tuple[str, int]] = []
    y = 180
    for group in group_order:
        headers.append((group, y - 55))
        for entry in group_to_entries[group]:
            rows.append((entry, y))
            y += 110
        y += 56

    intervals = [_wilson_interval(entry.passes, entry.total) for entry in entries]
    x_min = max(0.0, min(interval[0] for interval in intervals) - 0.12)
    x_max = min(1.0, max(interval[1] for interval in intervals) + 0.08)

    def value_to_x(value: float) -> int:
        span = x_max - x_min
        return int(plot_left + ((value - x_min) / span) * (plot_right - plot_left))

    draw.text((left, top), "50-task benchmark comparison", font=title_font, fill=INK)
    meta_label = "95% Wilson CI"
    meta_bbox = draw.textbbox((0, 0), meta_label, font=small_font)
    draw.text(
        (width - 70 - (meta_bbox[2] - meta_bbox[0]), top + 8),
        meta_label,
        font=small_font,
        fill=MUTED,
    )

    ticks = [0.35, 0.45, 0.55, 0.65, 0.75, 0.85]
    _draw_horizontal_axis(
        draw,
        x0=plot_left,
        x1=plot_right,
        y0=axis_y,
        tick_values=ticks,
        value_to_x=value_to_x,
        tick_font=tick_font,
        tick_formatter=lambda value: f"{int(round(value * 100))}%",
        show_grid_top=120,
        show_grid_bottom=axis_y - 12,
    )
    axis_label = "Pass rate on the common 50-task slice"
    axis_bbox = draw.textbbox((0, 0), axis_label, font=small_font)
    draw.text(
        ((plot_left + plot_right - (axis_bbox[2] - axis_bbox[0])) / 2, 1125),
        axis_label,
        font=small_font,
        fill=MUTED,
    )

    for idx, (group, header_y) in enumerate(headers):
        draw.text((plot_left, header_y), group, font=section_font, fill=SECTION)
        if idx > 0:
            separator_y = header_y - 28
            draw.line((plot_left, separator_y, plot_right, separator_y), fill=GRID, width=2)

    for entry, y in rows:
        ci_low, ci_high = _wilson_interval(entry.passes, entry.total)
        draw.line((value_to_x(ci_low), y, value_to_x(ci_high), y), fill=entry.color, width=6)
        dot_x = value_to_x(entry.rate)
        draw.ellipse((dot_x - 10, y - 10, dot_x + 10, y + 10), fill=entry.color)

        draw.text((label_right, y - 28), entry.label, font=body_bold, fill=INK, anchor="ra")
        wrapped = textwrap.fill(entry.definition, width=63)
        _draw_text(
            draw,
            (label_right, y + 8),
            wrapped,
            font=body_font,
            fill=MUTED,
            anchor="ra",
            spacing=6,
        )
        draw.text((value_left, y - 10), f"{entry.passes}/{entry.total}", font=body_bold, fill=INK)

    footer = (
        "Budget note: the Codex diagnostic uses a 30-minute per-task budget. "
        "The original and matched Phase II runs use 15 minutes per task, so time budget affects performance independently of routing."
        "\n"
        "Comparison note: the 23 to 28 change is inside the matched rerun. "
        "The older 29/50 original six-model market run comes from a separate earlier setup."
    )
    _draw_text(
        draw, (left, 1170), textwrap.fill(footer, width=128), font=small_font, fill=MUTED, spacing=6
    )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_png)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render split page-1 figures for calibration and live task performance."
    )
    parser.add_argument(
        "--baseline-calibration",
        type=Path,
        default=Path("docs/research/data/phase1/calibration_results.jsonl"),
    )
    parser.add_argument(
        "--self-knowledge-calibration",
        type=Path,
        default=Path(
            "runs/research/phase1/self_knowledge_direct_full_20260406T221032Z/"
            "calibration_results_complete_models.jsonl"
        ),
    )
    parser.add_argument(
        "--phase2-rollup",
        type=Path,
        default=Path("docs/research/data/phase2/phase2_rollup_50.json"),
    )
    parser.add_argument(
        "--market-vs-solo-summary",
        type=Path,
        default=Path("docs/research/data/phase2/market_vs_solo_summary.json"),
    )
    parser.add_argument(
        "--codex-summary",
        type=Path,
        default=Path("docs/research/data/phase2/codex_relaxed_gpt52_summary.json"),
    )
    parser.add_argument(
        "--phase2-report",
        type=Path,
        default=Path("docs/research/report/10_PHASE_2B_CENTRAL_ROUTER_BASELINE_2026-04-07.md"),
    )
    parser.add_argument(
        "--calibration-output-png",
        type=Path,
        default=Path("docs/research/report/data/page1_calibration_summary.png"),
    )
    parser.add_argument(
        "--live-output-png",
        type=Path,
        default=Path("docs/research/report/data/page1_live_performance_summary.png"),
    )
    args = parser.parse_args()

    baseline = _calibration_brier_by_key(args.baseline_calibration)
    self_knowledge = _calibration_brier_by_key(args.self_knowledge_calibration)
    direct_entry, self_knowledge_entry, delta = _paired_bootstrap_mean_ci(baseline, self_knowledge)
    live_entries = _load_live_entries(
        phase2_rollup_path=args.phase2_rollup,
        market_vs_solo_path=args.market_vs_solo_summary,
        codex_summary_path=args.codex_summary,
        phase2_report_path=args.phase2_report,
    )

    _render_calibration(
        entries=[direct_entry, self_knowledge_entry],
        delta=delta,
        output_png=args.calibration_output_png,
    )
    _render_live_performance(
        entries=live_entries,
        output_png=args.live_output_png,
    )

    print(f"calibration_png={args.calibration_output_png}")
    print(f"live_png={args.live_output_png}")


if __name__ == "__main__":
    main()
