from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_economy.sandbox import (
    apply_unified_diff,
    apply_unified_diff_path,
    build_patch_from_dirs,
    extract_file_blocks,
    extract_git_diff,
    parse_patch_changes,
    prune_generated_workspace_artifacts,
)


def test_apply_unified_diff_works_with_relative_cwd(tmp_path) -> None:
    start = Path.cwd()
    try:
        os.chdir(tmp_path)
        sandbox = Path("sandbox")
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "a.txt").write_text("hello\n", encoding="utf-8")

        patch_text = (
            "diff --git a/a.txt b/a.txt\n"
            "index e69de29..4b825dc 100644\n"
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1 +1 @@\n"
            "-hello\n"
            "+hello world\n"
        )
        apply_unified_diff(patch_text=patch_text, cwd=sandbox)
        assert (sandbox / "a.txt").read_text(encoding="utf-8") == "hello world\n"
    finally:
        os.chdir(start)


def test_apply_unified_diff_normalizes_corrupt_hunk_counts(tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "a.txt").write_text("hello\n", encoding="utf-8")

    patch_text = (
        "diff --git a/a.txt b/a.txt\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,2 +1,2 @@\n"
        "-hello\n"
        "+hello world\n"
    )
    patch_path = apply_unified_diff(patch_text=patch_text, cwd=sandbox)
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "hello world\n"
    assert "@@ -1,1 +1,1 @@" in patch_path.read_text(encoding="utf-8")


def test_build_patch_from_dirs_is_relativized_and_applyable(tmp_path) -> None:
    base_dir = tmp_path / "base"
    work_dir = tmp_path / "work"
    base_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)

    (base_dir / "a.txt").write_text("hello\n", encoding="utf-8")
    (work_dir / "a.txt").write_text("hello world\n", encoding="utf-8")
    (work_dir / "b.txt").write_text("new\n", encoding="utf-8")

    patch = build_patch_from_dirs(base_dir=base_dir, work_dir=work_dir)
    assert patch.touched_paths == ["a.txt", "b.txt"]
    assert str(tmp_path) not in patch.patch_text
    assert "diff --git a/a.txt b/a.txt" in patch.patch_text

    patch_path = tmp_path / "patch.diff"
    patch_path.write_text(patch.patch_text, encoding="utf-8")
    apply_unified_diff_path(patch_path=patch_path, cwd=base_dir)

    assert (base_dir / "a.txt").read_text(encoding="utf-8") == "hello world\n"
    assert (base_dir / "b.txt").read_text(encoding="utf-8") == "new\n"


def test_extract_file_blocks_strips_workspace_prefix() -> None:
    text = (
        "BEGIN_FILE runs/bench_snake_market/sandboxes/r0_T3/workspace/target/snakelite/render.py\n"
        "print('hi')\n"
        "END_FILE\n"
    )
    files = extract_file_blocks(text)
    assert sorted(files.keys()) == ["target/snakelite/render.py"]
    assert files["target/snakelite/render.py"] == "print('hi')\n"


def test_extract_git_diff_strips_workspace_prefix() -> None:
    raw = (
        "diff --git a/runs/bench_snake_market/sandboxes/r0_T3/workspace/target/snakelite/render.py "
        "b/runs/bench_snake_market/sandboxes/r0_T3/workspace/target/snakelite/render.py\n"
        "index e69de29..4b825dc 100644\n"
        "--- a/runs/bench_snake_market/sandboxes/r0_T3/workspace/target/snakelite/render.py\n"
        "+++ b/runs/bench_snake_market/sandboxes/r0_T3/workspace/target/snakelite/render.py\n"
        "@@ -0,0 +1 @@\n"
        "+print('hi')\n"
    )
    patch = extract_git_diff(raw)
    changes = parse_patch_changes(patch)
    assert [c.old_path for c in changes] == ["target/snakelite/render.py"]
    assert [c.new_path for c in changes] == ["target/snakelite/render.py"]


def test_build_patch_from_dirs_strips_cwd_relative_prefixes(tmp_path) -> None:
    start = Path.cwd()
    try:
        os.chdir(tmp_path)
        base_dir = tmp_path / "runs" / "bench" / "workspace"
        work_dir = tmp_path / "runs" / "bench" / "sandboxes" / "r0" / "workspace"
        (base_dir / "target").mkdir(parents=True)
        (work_dir / "target").mkdir(parents=True)
        (base_dir / "target" / "x.txt").write_text("hello\n", encoding="utf-8")
        (work_dir / "target" / "x.txt").write_text("hello world\n", encoding="utf-8")

        patch = build_patch_from_dirs(base_dir=base_dir, work_dir=work_dir)
        assert patch.touched_paths == ["target/x.txt"]
        assert "diff --git a/target/x.txt b/target/x.txt" in patch.patch_text
        assert "runs/" not in patch.patch_text
    finally:
        os.chdir(start)


def test_build_patch_from_dirs_ignores_generated_workspace_artifacts(tmp_path) -> None:
    base_dir = tmp_path / "base"
    work_dir = tmp_path / "work"
    (base_dir / "pkg").mkdir(parents=True)
    (work_dir / "pkg").mkdir(parents=True)
    (base_dir / "pkg" / "module.py").write_text("value = 1\n", encoding="utf-8")
    (work_dir / "pkg" / "module.py").write_text("value = 2\n", encoding="utf-8")
    (work_dir / "patch.diff").write_text("generated\n", encoding="utf-8")
    (work_dir / "predictions_demo.jsonl").write_text("{}\n", encoding="utf-8")
    (work_dir / "harness_demo.json").write_text("{}\n", encoding="utf-8")
    (work_dir / "agent-economy-market.demo.json").write_text("{}\n", encoding="utf-8")
    (work_dir / ".ae_harness_runner" / "report.json").parent.mkdir(parents=True)
    (work_dir / ".ae_harness_runner" / "report.json").write_text("{}\n", encoding="utf-8")
    (work_dir / "logs" / "run_evaluation" / "demo").mkdir(parents=True)
    (work_dir / "logs" / "run_evaluation" / "demo" / "run_instance.log").write_text(
        "log\n", encoding="utf-8"
    )

    patch = build_patch_from_dirs(base_dir=base_dir, work_dir=work_dir)

    assert patch.touched_paths == ["pkg/module.py"]
    assert "pkg/module.py" in patch.patch_text
    assert "patch.diff" not in patch.patch_text
    assert "predictions_demo.jsonl" not in patch.patch_text
    assert "harness_demo.json" not in patch.patch_text
    assert "agent-economy-market.demo.json" not in patch.patch_text
    assert ".ae_harness_runner" not in patch.patch_text
    assert "logs/run_evaluation" not in patch.patch_text


def test_prune_generated_workspace_artifacts_removes_benchmark_side_files(tmp_path) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "patch.diff").write_text("generated\n", encoding="utf-8")
    (workspace_dir / "predictions_demo.jsonl").write_text("{}\n", encoding="utf-8")
    (workspace_dir / "harness_demo.json").write_text("{}\n", encoding="utf-8")
    (workspace_dir / "agent-economy-market.demo.json").write_text("{}\n", encoding="utf-8")
    (workspace_dir / ".ae_harness_runner" / "report.json").parent.mkdir(parents=True)
    (workspace_dir / ".ae_harness_runner" / "report.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (workspace_dir / "logs" / "run_evaluation" / "demo").mkdir(parents=True)
    (workspace_dir / "logs" / "run_evaluation" / "demo" / "run_instance.log").write_text(
        "log\n", encoding="utf-8"
    )
    (workspace_dir / "pkg").mkdir()
    (workspace_dir / "pkg" / "module.py").write_text("value = 1\n", encoding="utf-8")

    removed = prune_generated_workspace_artifacts(workspace_dir=workspace_dir)

    assert removed == [
        ".ae_harness_runner",
        "agent-economy-market.demo.json",
        "harness_demo.json",
        "logs/run_evaluation",
        "patch.diff",
        "predictions_demo.jsonl",
    ]
    assert not (workspace_dir / "patch.diff").exists()
    assert not (workspace_dir / "predictions_demo.jsonl").exists()
    assert not (workspace_dir / "harness_demo.json").exists()
    assert not (workspace_dir / "agent-economy-market.demo.json").exists()
    assert not (workspace_dir / ".ae_harness_runner").exists()
    assert not (workspace_dir / "logs" / "run_evaluation").exists()
    assert (workspace_dir / "pkg" / "module.py").exists()


def test_build_patch_from_dirs_prefers_git_repo_and_skips_ignored_files(tmp_path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    subprocess.run(["git", "init"], cwd=work_dir, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"],
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tests"],
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
    )

    (work_dir / ".gitignore").write_text(".ae_harness_runner/\nignored.log\n", encoding="utf-8")
    (work_dir / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=work_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=work_dir, check=True, capture_output=True)

    (work_dir / "tracked.txt").write_text("after\n", encoding="utf-8")
    (work_dir / "added.txt").write_text("new file\n", encoding="utf-8")
    (work_dir / "ignored.log").write_text("skip me\n", encoding="utf-8")
    (work_dir / ".ae_harness_runner" / "report.json").parent.mkdir(parents=True)
    (work_dir / ".ae_harness_runner" / "report.json").write_text("{}\n", encoding="utf-8")

    patch = build_patch_from_dirs(base_dir=tmp_path / "base", work_dir=work_dir)

    assert patch.touched_paths == ["added.txt", "tracked.txt"]
    assert "tracked.txt" in patch.patch_text
    assert "added.txt" in patch.patch_text
    assert "ignored.log" not in patch.patch_text
    assert ".ae_harness_runner" not in patch.patch_text
