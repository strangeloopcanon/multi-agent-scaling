from __future__ import annotations

import pytest

from agent_economy.schemas import WorkerType
from agent_economy.worker_specs import load_worker_pool_from_json


def test_load_worker_pool_from_json_supports_mapping_backcompat() -> None:
    pool = load_worker_pool_from_json({"w1": "gpt-4o"})
    assert len(pool.workers) == 1
    assert pool.workers[0].worker_id == "w1"
    assert pool.workers[0].worker_type == WorkerType.MODEL_AGENT
    assert pool.workers[0].model_ref == "gpt-4o"
    assert pool.command_specs == {}


def test_load_worker_pool_from_json_supports_mixed_list() -> None:
    pool = load_worker_pool_from_json(
        [
            {"worker_id": "w1", "model_ref": "gpt-4o"},
            {
                "worker_id": "w2",
                "model_ref": "openai:gpt-5.4",
                "exec_cmd": "echo hi",
                "fixed_bid": {"ask": 10, "p_success": 0.8, "eta_minutes": 5},
            },
        ]
    )
    assert [w.worker_id for w in pool.workers] == ["w1", "w2"]
    assert pool.workers[0].worker_type == WorkerType.MODEL_AGENT
    assert pool.workers[1].worker_type == WorkerType.EXTERNAL_WORKER
    assert pool.workers[1].model_ref == "openai:gpt-5.4"
    assert pool.command_specs["w2"].exec_cmd == "echo hi"


def test_load_worker_pool_from_json_rejects_duplicate_worker_id() -> None:
    with pytest.raises(ValueError, match="duplicate worker_id"):
        load_worker_pool_from_json(
            [
                {"worker_id": "w1", "model_ref": "gpt-4o"},
                {"worker_id": "w1", "model_ref": "gpt-5-mini"},
            ]
        )


def test_command_worker_requires_bid_source() -> None:
    with pytest.raises(ValueError, match="bid_cmd or fixed_bid"):
        load_worker_pool_from_json([{"worker_id": "w1", "exec_cmd": "echo hi"}])
