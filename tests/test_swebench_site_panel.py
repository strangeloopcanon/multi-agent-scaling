from __future__ import annotations

import json
from pathlib import Path

from agent_economy.research.swebench_site_panel import (
    SwebenchSiteModelSpec,
    build_site_manifest,
    collect_site_rows,
    load_task_ids,
)


def test_load_task_ids_from_phase2_outcomes(tmp_path: Path) -> None:
    task_list = tmp_path / "per_task_outcomes.jsonl"
    task_list.write_text(
        "\n".join(
            [
                json.dumps({"instance_id": "astropy__astropy-14182"}),
                json.dumps({"instance_id": "django__django-11179"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_task_ids(task_list) == [
        "astropy__astropy-14182",
        "django__django-11179",
    ]


def test_collect_site_rows_filters_to_tasks_and_model_specs() -> None:
    payload = {
        "leaderboards": [
            {
                "name": "bash-only",
                "results": [
                    {
                        "folder": "site-folder",
                        "name": "Site Model",
                        "date": "2026-01-02",
                        "resolved": 50.0,
                        "tags": ["Model: site-model"],
                        "per_instance_details": {
                            "astropy__astropy-14182": {
                                "resolved": True,
                                "cost": 0.25,
                                "api_calls": 3,
                            },
                            "django__django-11179": {
                                "resolved": False,
                                "cost": 0.5,
                                "api_calls": 4,
                            },
                        },
                    }
                ],
            }
        ]
    }

    rows, missing = collect_site_rows(
        payload,
        task_ids=["astropy__astropy-14182"],
        source_url="https://example.test/leaderboards.json",
        model_specs=(
            SwebenchSiteModelSpec(model="provider:model", folder="site-folder"),
            SwebenchSiteModelSpec(
                model="provider:missing",
                folder=None,
                missing_note="not on site",
            ),
        ),
    )

    assert len(rows) == 1
    assert rows[0].core_csv_row() == {
        "model": "provider:model",
        "task": "astropy__astropy-14182",
        "task family": "astropy/astropy",
        "success": "true",
        "token consumption": "",
    }
    assert rows[0].source_csv_row()["site_cost"] == 0.25
    assert rows[0].source_csv_row()["site_api_calls"] == 3
    assert missing == {"provider:missing": "not on site"}


def test_build_site_manifest_records_missing_tokens_and_models() -> None:
    payload = {
        "leaderboards": [
            {
                "name": "bash-only",
                "results": [
                    {
                        "folder": "site-folder",
                        "name": "Site Model",
                        "date": "2026-01-02",
                        "resolved": 50.0,
                        "tags": [],
                        "per_instance_details": {
                            "astropy__astropy-14182": {
                                "resolved": True,
                                "cost": 0.25,
                                "api_calls": 3,
                            }
                        },
                    }
                ],
            }
        ]
    }
    rows, missing = collect_site_rows(
        payload,
        task_ids=["astropy__astropy-14182"],
        model_specs=(SwebenchSiteModelSpec(model="provider:model", folder="site-folder"),),
    )

    manifest = build_site_manifest(
        rows,
        task_ids=["astropy__astropy-14182"],
        missing_models={**missing, "provider:missing": "not on site"},
    )

    assert manifest["token_consumption_available"] is False
    assert manifest["requested_task_count"] == 1
    assert manifest["models"]["provider:model"]["pass_count"] == 1
    assert manifest["models"]["provider:model"]["site_api_calls_sum"] == 3
    assert manifest["missing_models"] == {"provider:missing": "not on site"}
