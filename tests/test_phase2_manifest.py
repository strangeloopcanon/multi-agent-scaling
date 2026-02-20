from __future__ import annotations

import json
from pathlib import Path


def test_phase2_93_manifest_is_unique_and_stable() -> None:
    manifest_path = Path("benchmarks/swebench/phase2_93_manifest_v1.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = [str(x) for x in payload.get("instance_ids") or []]

    assert len(ids) == 93
    assert len(set(ids)) == 93
    assert ids[0] == "astropy__astropy-14182"
    assert ids[-1] == "sympy__sympy-24213"
