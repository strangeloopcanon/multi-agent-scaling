from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_economy.research.calibration import (
        CalibrationRecord as CalibrationRecord,
        CalibrationResponse as CalibrationResponse,
        PromptStrategy as PromptStrategy,
        build_calibration_prompt as build_calibration_prompt,
    )
    from agent_economy.research.calibration_metrics import (
        brier_score as brier_score,
        expected_calibration_error as expected_calibration_error,
        reliability_bins as reliability_bins,
        summarize_calibration as summarize_calibration,
    )
    from agent_economy.research.market_metrics import (
        summarize_market_run as summarize_market_run,
        summarize_market_runs as summarize_market_runs,
    )
    from agent_economy.research.reserve_auction import (
        compute_breakeven_bid as compute_breakeven_bid,
        simulate_reserve_auction as simulate_reserve_auction,
        summarize_auction_results as summarize_auction_results,
    )
    from agent_economy.research.study_join import join_phase_metrics as join_phase_metrics
    from agent_economy.research.swebench import (
        DEFAULT_PHASE2_MANIFEST_PATH as DEFAULT_PHASE2_MANIFEST_PATH,
        Phase2TaskManifest as Phase2TaskManifest,
        SwebenchInstance as SwebenchInstance,
        load_phase2_manifest as load_phase2_manifest,
        load_phase2_task_ids as load_phase2_task_ids,
        load_swebench_lite_instances_by_id as load_swebench_lite_instances_by_id,
        load_swebench_subset as load_swebench_subset,
        materialize_instance_workspace as materialize_instance_workspace,
        materialize_real_instance_workspace as materialize_real_instance_workspace,
        suggest_files_hint as suggest_files_hint,
        to_phase2_task_spec as to_phase2_task_spec,
        to_task_spec as to_task_spec,
        write_phase2_instance_scenario as write_phase2_instance_scenario,
    )

__all__ = [
    "CalibrationRecord",
    "CalibrationResponse",
    "PromptStrategy",
    "build_calibration_prompt",
    "brier_score",
    "expected_calibration_error",
    "reliability_bins",
    "summarize_calibration",
    "summarize_market_run",
    "summarize_market_runs",
    "DEFAULT_PHASE2_MANIFEST_PATH",
    "Phase2TaskManifest",
    "SwebenchInstance",
    "load_phase2_manifest",
    "load_phase2_task_ids",
    "load_swebench_lite_instances_by_id",
    "load_swebench_subset",
    "materialize_instance_workspace",
    "materialize_real_instance_workspace",
    "suggest_files_hint",
    "to_phase2_task_spec",
    "to_task_spec",
    "write_phase2_instance_scenario",
    "join_phase_metrics",
    "compute_breakeven_bid",
    "simulate_reserve_auction",
    "summarize_auction_results",
]

_SUBMODULE_BY_ATTR: dict[str, str] = {
    "CalibrationRecord": "agent_economy.research.calibration",
    "CalibrationResponse": "agent_economy.research.calibration",
    "PromptStrategy": "agent_economy.research.calibration",
    "build_calibration_prompt": "agent_economy.research.calibration",
    "brier_score": "agent_economy.research.calibration_metrics",
    "expected_calibration_error": "agent_economy.research.calibration_metrics",
    "reliability_bins": "agent_economy.research.calibration_metrics",
    "summarize_calibration": "agent_economy.research.calibration_metrics",
    "summarize_market_run": "agent_economy.research.market_metrics",
    "summarize_market_runs": "agent_economy.research.market_metrics",
    "DEFAULT_PHASE2_MANIFEST_PATH": "agent_economy.research.swebench",
    "Phase2TaskManifest": "agent_economy.research.swebench",
    "SwebenchInstance": "agent_economy.research.swebench",
    "load_phase2_manifest": "agent_economy.research.swebench",
    "load_phase2_task_ids": "agent_economy.research.swebench",
    "load_swebench_lite_instances_by_id": "agent_economy.research.swebench",
    "load_swebench_subset": "agent_economy.research.swebench",
    "materialize_instance_workspace": "agent_economy.research.swebench",
    "materialize_real_instance_workspace": "agent_economy.research.swebench",
    "suggest_files_hint": "agent_economy.research.swebench",
    "to_phase2_task_spec": "agent_economy.research.swebench",
    "to_task_spec": "agent_economy.research.swebench",
    "write_phase2_instance_scenario": "agent_economy.research.swebench",
    "join_phase_metrics": "agent_economy.research.study_join",
    "compute_breakeven_bid": "agent_economy.research.reserve_auction",
    "simulate_reserve_auction": "agent_economy.research.reserve_auction",
    "summarize_auction_results": "agent_economy.research.reserve_auction",
}


def __getattr__(name: str) -> object:
    module_path = _SUBMODULE_BY_ATTR.get(name)
    if module_path is not None:
        module = importlib.import_module(module_path)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
