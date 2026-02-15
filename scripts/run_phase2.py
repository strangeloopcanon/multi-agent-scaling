from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_economy.command_workers import CommandBidder, CommandExecutor, CommandExecutorSettings
from agent_economy.config import load_settings
from agent_economy.cost_estimator import ExpectedCostEstimator
from agent_economy.costing import load_pricing_from_env
from agent_economy.engine import ClearinghouseEngine
from agent_economy.finalize import release_judges_holdbacks
from agent_economy.ledger import HashChainedLedger
from agent_economy.main import (
    _engine_settings,
    _llm_router_for_workers,
)
from agent_economy.openai_bidder import OpenAIBidder
from agent_economy.openai_executor import ExecutorSettings, OpenAIExecutor
from agent_economy.research.market_metrics import summarize_market_runs
from agent_economy.scenario import load_scenario
from agent_economy.state import SettlementPolicy, replay_ledger
from agent_economy.worker_specs import load_worker_pool_from_path
from agent_economy.worker_state import (
    default_state_path,
    load_state,
    update_state_from_run,
    save_state,
)
from agent_economy.worker_state import extract_patch_usage_samples
from agent_economy.worker_mux import MultiplexBidder, MultiplexExecutor


DEFAULT_MODELS = [
    "openai:gpt-5-mini",
    "openai:gpt-5.2",
    "openai:gpt-5.2-pro",
    "openai:gpt-4o",
    "anthropic:claude-sonnet-4-5",
    "google:gemini-2.5-pro",
]

DEFAULT_SCENARIOS = {
    "swebench": Path("scenarios/swebench_semver_bug.yaml"),
    "synthesis": Path("scenarios/synthesis_reasoning_pilot.yaml"),
}


@dataclass(frozen=True)
class RunSpec:
    benchmark: str
    mode: str  # solo|market
    settlement_mode: str  # reputation|direct_penalty
    repeat: int
    scenario_path: str
    model_ref: str | None = None


def _now_tag() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def _norm_csv(values: str) -> list[str]:
    return [v.strip() for v in str(values).split(",") if v.strip()]


def _provider(model_ref: str) -> str:
    if ":" in model_ref:
        return model_ref.split(":", 1)[0].strip().lower()
    return "openai"


def _require_credentials(model_refs: list[str]) -> None:
    settings = load_settings()
    providers = {_provider(m) for m in model_refs}

    missing: list[str] = []
    if "openai" in providers and not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if "anthropic" in providers and not settings.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if "google" in providers and not settings.google_api_key:
        missing.append("GOOGLE_API_KEY or GEMINI_API_KEY")

    if missing:
        raise SystemExit("missing provider credentials: " + ", ".join(missing))


def build_run_matrix(
    *,
    benchmarks: list[str],
    models: list[str],
    repeats: int,
    scenario_paths: dict[str, Path],
) -> list[RunSpec]:
    out: list[RunSpec] = []
    for benchmark in benchmarks:
        scenario = scenario_paths.get(benchmark)
        if scenario is None:
            raise ValueError(f"unknown benchmark: {benchmark}")

        for repeat in range(1, repeats + 1):
            for model_ref in models:
                out.append(
                    RunSpec(
                        benchmark=benchmark,
                        mode="solo",
                        settlement_mode="reputation",
                        repeat=repeat,
                        scenario_path=str(scenario),
                        model_ref=model_ref,
                    )
                )

            out.append(
                RunSpec(
                    benchmark=benchmark,
                    mode="market",
                    settlement_mode="reputation",
                    repeat=repeat,
                    scenario_path=str(scenario),
                    model_ref=None,
                )
            )
            out.append(
                RunSpec(
                    benchmark=benchmark,
                    mode="market",
                    settlement_mode="direct_penalty",
                    repeat=repeat,
                    scenario_path=str(scenario),
                    model_ref=None,
                )
            )
    return out


def _select_workers(*, spec: RunSpec, workers_path: Path):
    pool = load_worker_pool_from_path(workers_path)
    workers = list(pool.workers)
    command_specs = dict(pool.command_specs)

    if spec.mode == "solo":
        if not spec.model_ref:
            raise ValueError("solo run spec requires model_ref")
        selected = [w for w in workers if (w.model_ref or "") == spec.model_ref]
        if not selected:
            raise ValueError(f"model_ref not found in workers file: {spec.model_ref}")
        workers = selected

    return workers, command_specs


def _run_one_spec(
    *,
    spec: RunSpec,
    run_dir: Path,
    workers_path: Path,
    rounds: int,
    concurrency: int,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=False)

    scenario = load_scenario(Path(spec.scenario_path))
    if scenario.template_dir is None:
        raise ValueError(f"scenario has no template_dir: {spec.scenario_path}")

    workspace_dir = run_dir / "workspace"
    shutil.copytree(scenario.template_dir, workspace_dir)

    workers, command_specs = _select_workers(spec=spec, workers_path=workers_path)

    settlement = SettlementPolicy(penalty_mode=spec.settlement_mode)
    ledger = HashChainedLedger(run_dir / "ledger.jsonl")
    engine = ClearinghouseEngine(
        ledger=ledger,
        settings=_engine_settings(max_concurrency=concurrency),
        settlement=settlement,
    )
    run_id = run_dir.name
    engine.create_run(run_id=run_id, workers=workers, tasks=scenario.tasks)

    settings = load_settings()
    llm = _llm_router_for_workers(settings=settings, workers=workers)

    has_external = any(w.worker_type.value == "external_worker" for w in workers)

    judge_workers = [w.worker_id for w in workers][:3]
    model_bidder = OpenAIBidder(
        llm=llm,
        payment_rule=replay_ledger(events=list(ledger.iter_events())).payment_rule,
        max_bids=2,
    )
    model_executor = OpenAIExecutor(
        llm=llm,
        workspace_dir=workspace_dir,
        run_dir=run_dir,
        workers=workers,
        command_specs=command_specs,
        settings=ExecutorSettings(judge_workers=judge_workers),
    )

    ext_bidder = (
        None
        if not has_external
        else CommandBidder(
            workspace_dir=workspace_dir,
            payment_rule=replay_ledger(events=list(ledger.iter_events())).payment_rule,
            specs=command_specs,
            max_bids=2,
        )
    )
    ext_executor = (
        None
        if not has_external
        else CommandExecutor(
            workspace_dir=workspace_dir,
            run_dir=run_dir,
            workers=workers,
            specs=command_specs,
            settings=CommandExecutorSettings(judge_workers=judge_workers),
            llm=llm,
        )
    )

    bidder = MultiplexBidder(model_bidder=model_bidder, external_bidder=ext_bidder)
    executor = MultiplexExecutor(model_executor=model_executor, external_executor=ext_executor)
    estimator = ExpectedCostEstimator(
        state=load_state(default_state_path()),
        pricing=load_pricing_from_env(),
    )

    events = list(ledger.iter_events())
    state = replay_ledger(events=events, settlement=settlement)
    for _ in range(rounds):
        before = len(events)
        engine.step(bidder=bidder, executor=executor, cost_estimator=estimator)
        events = list(ledger.iter_events())
        if len(events) == before:
            break
        state = replay_ledger(events=events, settlement=settlement)
        if state.tasks and all(t.status in {"DONE", "REVIEW"} for t in state.tasks.values()):
            break

    release_judges_holdbacks(ledger=ledger)
    events = list(ledger.iter_events())
    state = replay_ledger(events=events, settlement=settlement)

    (run_dir / "state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")

    persisted = load_state(default_state_path())
    persisted = update_state_from_run(
        state=persisted,
        run_workers=state.workers,
        patch_usages=extract_patch_usage_samples(events=events),
    )
    save_state(default_state_path(), persisted)

    run_config = {
        "run_id": run_id,
        "scenario_path": str(Path(spec.scenario_path).resolve()),
        "workspace_dir": str(workspace_dir.resolve()),
        "workers": [w.model_dump() for w in workers],
        "mode": spec.mode,
        "settlement_mode": spec.settlement_mode,
        "repeat": spec.repeat,
        "benchmark": spec.benchmark,
        "model_ref": spec.model_ref,
    }
    (run_dir / "run_config.json").write_text(json.dumps(run_config, indent=2), encoding="utf-8")

    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase II matrix experiments")
    parser.add_argument(
        "--benchmarks",
        default="swebench,synthesis",
        help="comma-separated benchmark aliases",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="comma-separated model refs",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--workers",
        type=Path,
        default=Path("benchmarks/workers_phase2_mixed.json"),
        help="worker pool JSON path",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/research/phase2") / _now_tag(),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    benchmarks = _norm_csv(args.benchmarks)
    models = _norm_csv(args.models)

    scenario_paths = dict(DEFAULT_SCENARIOS)
    matrix = build_run_matrix(
        benchmarks=benchmarks,
        models=models,
        repeats=int(args.repeats),
        scenario_paths=scenario_paths,
    )

    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "run_matrix.json").write_text(
        json.dumps([asdict(r) for r in matrix], indent=2),
        encoding="utf-8",
    )

    if not args.execute:
        print(f"matrix_size={len(matrix)}")
        print(f"run_matrix={args.output_root / 'run_matrix.json'}")
        return

    _require_credentials(models)

    completed: list[Path] = []
    for spec in matrix:
        run_name = (
            f"{spec.benchmark}_{spec.mode}_{spec.settlement_mode}_"
            f"r{spec.repeat}_{(spec.model_ref or 'all').replace(':', '_')}"
        )
        run_dir = args.output_root / run_name
        if run_dir.exists():
            if not args.overwrite:
                raise SystemExit(f"run dir already exists: {run_dir}")
            shutil.rmtree(run_dir)

        completed.append(
            _run_one_spec(
                spec=spec,
                run_dir=run_dir,
                workers_path=Path(args.workers),
                rounds=int(args.rounds),
                concurrency=int(args.concurrency),
            )
        )

    summaries = summarize_market_runs(run_dirs=completed)
    (args.output_root / "market_run_summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"completed_runs={len(completed)}")
    print(f"summaries={args.output_root / 'market_run_summaries.json'}")


if __name__ == "__main__":
    main()
