from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agent_economy.jsonutil import stable_json_dumps
from agent_economy.schemas import ArtifactRef
from agent_economy.verify import CommandResult
from agent_economy.workspace_ignore import copytree_ignore


@dataclass(frozen=True)
class PatchFileChange:
    old_path: str | None
    new_path: str | None
    kind: str  # "modify" | "add" | "delete" | "rename"


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")


def _normalize_relpath(path: str) -> str:
    rel = str(path or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def is_generated_workspace_artifact(path: str) -> bool:
    rel = _normalize_relpath(path)
    if not rel:
        return False
    if rel == "patch.diff":
        return True
    if rel == ".ae_harness_runner" or rel.startswith(".ae_harness_runner/"):
        return True
    if rel == "logs/run_evaluation" or rel.startswith("logs/run_evaluation/"):
        return True

    name = Path(rel).name
    if re.fullmatch(r"predictions_[^/]+\.jsonl", name):
        return True
    if re.fullmatch(r"harness_[^/]+\.json", name):
        return True
    if re.fullmatch(r"agent-economy-market\.[^/]+\.json", name):
        return True
    return False


def prune_generated_workspace_artifacts(*, workspace_dir: Path) -> list[str]:
    removed: list[str] = []
    candidates = [
        workspace_dir / "patch.diff",
        workspace_dir / ".ae_harness_runner",
        workspace_dir / "logs" / "run_evaluation",
        *workspace_dir.glob("predictions_*.jsonl"),
        *workspace_dir.glob("harness_*.json"),
        *workspace_dir.glob("agent-economy-market.*.json"),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve() if candidate.exists() else candidate
        if key in seen:
            continue
        seen.add(key)
        if not candidate.exists():
            continue
        rel = str(candidate.relative_to(workspace_dir))
        if not is_generated_workspace_artifact(rel):
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()
        removed.append(rel)
    return sorted(set(removed))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_patch_changes(patch_text: str) -> list[PatchFileChange]:
    changes: list[PatchFileChange] = []
    cur_old: str | None = None
    cur_new: str | None = None
    cur_kind = "modify"

    def flush() -> None:
        nonlocal cur_old, cur_new, cur_kind
        if cur_old is None and cur_new is None:
            return
        changes.append(PatchFileChange(old_path=cur_old, new_path=cur_new, kind=cur_kind))
        cur_old = None
        cur_new = None
        cur_kind = "modify"

    for line in patch_text.splitlines():
        m = _DIFF_HEADER_RE.match(line)
        if m:
            flush()
            cur_old = m.group(1)
            cur_new = m.group(2)
            cur_kind = "modify"
            continue

        if line.startswith("new file mode "):
            cur_kind = "add"
            continue

        if line.startswith("deleted file mode "):
            cur_kind = "delete"
            continue

        if line.startswith("--- "):
            if line == "--- /dev/null":
                cur_kind = "add"
            continue

        if line.startswith("+++ "):
            if line == "+++ /dev/null":
                cur_kind = "delete"
            continue

        if line.startswith("rename from "):
            cur_kind = "rename"
            cur_old = line.removeprefix("rename from ").strip()
            continue

        if line.startswith("rename to "):
            cur_kind = "rename"
            cur_new = line.removeprefix("rename to ").strip()
            continue

    flush()
    return changes


def normalize_unified_diff_hunk_counts(patch_text: str) -> str:
    lines = patch_text.splitlines(keepends=True)
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip("\n")
        m = _HUNK_HEADER_RE.match(line)
        if not m:
            out.append(raw)
            i += 1
            continue

        old_start_s, old_count_s, new_start_s, new_count_s, suffix = m.groups()
        old_count_in = int(old_count_s) if old_count_s is not None else 1
        new_count_in = int(new_count_s) if new_count_s is not None else 1
        old_count = 0
        new_count = 0

        j = i + 1
        while j < len(lines):
            body_raw = lines[j]
            body = body_raw.rstrip("\n")
            if body.startswith("diff --git "):
                break
            if _HUNK_HEADER_RE.match(body):
                break
            if body.startswith("\\"):
                j += 1
                continue
            if body.startswith(" "):
                old_count += 1
                new_count += 1
            elif body.startswith("-"):
                old_count += 1
            elif body.startswith("+"):
                new_count += 1
            j += 1

        if old_count_in != old_count or new_count_in != new_count:
            out.append(f"@@ -{old_start_s},{old_count} +{new_start_s},{new_count} @@{suffix}\n")
            changed = True
        else:
            out.append(raw)
        i += 1

    if not out:
        return patch_text
    normalized = "".join(out)
    return normalized if changed else patch_text


def _is_safe_relpath(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    if "\x00" in path:
        return False
    parts = Path(path).parts
    return not any(p in ("..",) for p in parts)


def _normalize_allowed(allowed: list[str]) -> list[str]:
    out: list[str] = []
    for a in allowed:
        a = str(a).strip()
        if a in {".", "./"}:
            out.append("")
            continue
        if a.startswith("./"):
            a = a.removeprefix("./")
        out.append(a)
    return out


def enforce_allowed_paths(*, paths: list[str], allowed: list[str]) -> None:
    allowed_n = _normalize_allowed(allowed)
    for p in paths:
        if not _is_safe_relpath(p):
            raise ValueError(f"unsafe patch path: {p!r}")
        if "" in allowed_n:
            continue
        if not any(p == a or p.startswith(a.rstrip("/") + "/") for a in allowed_n):
            raise ValueError(f"patch touches path outside allowed set: {p}")


def apply_unified_diff(*, patch_text: str, cwd: Path, patch_path: Path | None = None) -> Path:
    cwd.mkdir(parents=True, exist_ok=True)
    patch_path = patch_path or (cwd / "patch.diff")
    patch_text = patch_text.rstrip() + "\n"

    def _git_apply(check_only: bool) -> None:
        cmd = ["git", "apply", "--whitespace=nowarn"]
        if check_only:
            cmd.append("--check")
        cmd.append(str(patch_path.resolve()))
        subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        )

    write_text_atomic(patch_path, patch_text)
    try:
        _git_apply(check_only=True)
    except subprocess.CalledProcessError as e:
        if e.returncode == 128 and "corrupt patch" in (e.stderr or ""):
            normalized = normalize_unified_diff_hunk_counts(patch_text)
            if normalized != patch_text:
                patch_text = normalized
                write_text_atomic(patch_path, patch_text)
                _git_apply(check_only=True)
            else:
                raise
        else:
            raise
    _git_apply(check_only=False)
    return patch_path


def apply_unified_diff_text(*, patch_text: str, cwd: Path) -> None:
    apply_unified_diff(patch_text=patch_text, cwd=cwd)


def apply_unified_diff_path(*, patch_path: Path, cwd: Path) -> None:
    def _git_apply(check_only: bool) -> None:
        cmd = ["git", "apply", "--whitespace=nowarn"]
        if check_only:
            cmd.append("--check")
        cmd.append(str(patch_path.resolve()))
        subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        )

    _git_apply(check_only=True)
    _git_apply(check_only=False)


@dataclass(frozen=True)
class PatchBuildResult:
    patch_text: str
    touched_paths: list[str]


def _tail(text: str, *, max_chars: int = 2000) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16] + "\n... (truncated)\n"


def _strip_dir_prefix(path: str, *, prefixes: list[str]) -> str:
    path = path.strip()
    for prefix in prefixes:
        prefix = prefix.rstrip("/")
        if not prefix:
            continue
        if path == prefix:
            return ""
        if path.startswith(prefix + "/"):
            return path[len(prefix) + 1 :]
    return path


def _canonicalize_llm_patch_path(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return p
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if "/workspace/" in p:
        p = p.split("/workspace/")[-1]
    elif p.startswith("workspace/"):
        p = p.removeprefix("workspace/")
    return p


def _canonicalize_unified_diff_paths(patch_text: str) -> str:
    out_lines: list[str] = []
    changed = False

    for raw in patch_text.splitlines():
        line = raw

        if line.startswith("diff --git "):
            parts = line.split(" ", 3)
            if len(parts) == 4 and parts[2].startswith("a/") and parts[3].startswith("b/"):
                left_in = parts[2][2:]
                right_in = parts[3][2:]
                left = _canonicalize_llm_patch_path(left_in)
                right = _canonicalize_llm_patch_path(right_in)
                changed = changed or left != left_in or right != right_in
                out_lines.append(f"diff --git a/{left} b/{right}")
                continue

        if line.startswith("--- a/"):
            left_in = line.removeprefix("--- a/")
            left = _canonicalize_llm_patch_path(left_in)
            changed = changed or left != left_in
            out_lines.append(f"--- a/{left}")
            continue

        if line.startswith("+++ b/"):
            right_in = line.removeprefix("+++ b/")
            right = _canonicalize_llm_patch_path(right_in)
            changed = changed or right != right_in
            out_lines.append(f"+++ b/{right}")
            continue

        if line.startswith("rename from "):
            path_in = line.removeprefix("rename from ")
            path = _canonicalize_llm_patch_path(path_in)
            changed = changed or path != path_in
            out_lines.append(f"rename from {path}")
            continue

        if line.startswith("rename to "):
            path_in = line.removeprefix("rename to ")
            path = _canonicalize_llm_patch_path(path_in)
            changed = changed or path != path_in
            out_lines.append(f"rename to {path}")
            continue

        out_lines.append(raw)

    out = "\n".join(out_lines).rstrip() + "\n"
    return out if changed else patch_text


def _relativize_no_index_diff(*, patch_text: str, base_dir: Path, work_dir: Path) -> str:
    base_p = base_dir.resolve()
    work_p = work_dir.resolve()
    base = str(base_p)
    work = str(work_p)
    prefixes = [base, base.lstrip("/"), work, work.lstrip("/")]

    cwd = Path.cwd().resolve()
    for p in [base_p, work_p]:
        try:
            prefixes.append(str(p.relative_to(cwd)))
        except Exception:
            continue

    out_lines: list[str] = []
    for raw in patch_text.splitlines():
        line = raw.rstrip("\n")

        if line.startswith("diff --git "):
            parts = line.split(" ", 3)
            if len(parts) == 4 and parts[2].startswith("a/") and parts[3].startswith("b/"):
                left = _strip_dir_prefix(parts[2][2:], prefixes=prefixes)
                right = _strip_dir_prefix(parts[3][2:], prefixes=prefixes)
                out_lines.append(f"diff --git a/{left} b/{right}")
                continue

        if line.startswith("--- a/"):
            left = _strip_dir_prefix(line.removeprefix("--- a/"), prefixes=prefixes)
            out_lines.append(f"--- a/{left}")
            continue
        if line.startswith("+++ b/"):
            right = _strip_dir_prefix(line.removeprefix("+++ b/"), prefixes=prefixes)
            out_lines.append(f"+++ b/{right}")
            continue

        if line.startswith("rename from "):
            path = _strip_dir_prefix(line.removeprefix("rename from "), prefixes=prefixes)
            out_lines.append(f"rename from {path}")
            continue
        if line.startswith("rename to "):
            path = _strip_dir_prefix(line.removeprefix("rename to "), prefixes=prefixes)
            out_lines.append(f"rename to {path}")
            continue

        out_lines.append(raw)

    return "\n".join(out_lines).rstrip() + "\n"


def _filter_generated_artifacts_from_patch(patch_text: str) -> str:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for raw in patch_text.splitlines(keepends=True):
        if raw.startswith("diff --git "):
            if cur:
                blocks.append(cur)
            cur = [raw]
            continue
        if cur:
            cur.append(raw)
    if cur:
        blocks.append(cur)

    if not blocks:
        return patch_text

    kept_blocks: list[str] = []
    for block in blocks:
        block_text = "".join(block)
        changes = parse_patch_changes(block_text)
        paths = [p for change in changes for p in (change.old_path, change.new_path) if p]
        if paths and all(is_generated_workspace_artifact(path) for path in paths):
            continue
        kept_blocks.append(block_text)
    return "".join(kept_blocks)


def _run_git_diff(*, cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc


def _is_git_worktree(*, cwd: Path) -> bool:
    proc = _run_git_diff(cwd=cwd, args=["rev-parse", "--is-inside-work-tree"])
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _build_patch_from_git_worktree(*, work_dir: Path) -> PatchBuildResult:
    tracked = _run_git_diff(
        cwd=work_dir,
        args=["diff", "--no-ext-diff", "--binary", "--relative", "HEAD", "--"],
    )
    if tracked.returncode not in {0, 1}:
        raise RuntimeError(f"git diff failed: rc={tracked.returncode}\n{_tail(tracked.stderr)}")

    untracked = _run_git_diff(
        cwd=work_dir,
        args=["ls-files", "--others", "--exclude-standard", "-z", "--"],
    )
    if untracked.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed: rc={untracked.returncode}\n{_tail(untracked.stderr)}"
        )

    blocks: list[str] = []
    if tracked.stdout.strip():
        blocks.append(tracked.stdout)

    for raw_path in sorted(p for p in untracked.stdout.split("\0") if p):
        rel_path = _normalize_relpath(raw_path)
        if not rel_path or is_generated_workspace_artifact(rel_path):
            continue

        add_proc = _run_git_diff(
            cwd=work_dir,
            args=["diff", "--no-index", "--binary", "--no-ext-diff", "--", "/dev/null", rel_path],
        )
        if add_proc.returncode not in {0, 1}:
            raise RuntimeError(
                f"git diff --no-index failed: rc={add_proc.returncode}\n{_tail(add_proc.stderr)}"
            )
        if add_proc.stdout.strip():
            blocks.append(add_proc.stdout)

    patch_text = "".join(blocks)
    if not patch_text.strip():
        return PatchBuildResult(patch_text="", touched_paths=[])

    patch_text = _filter_generated_artifacts_from_patch(patch_text)
    if not patch_text.strip():
        return PatchBuildResult(patch_text="", touched_paths=[])

    changes = parse_patch_changes(patch_text)
    touched = sorted({p for ch in changes for p in (ch.old_path, ch.new_path) if p is not None})
    return PatchBuildResult(patch_text=patch_text, touched_paths=touched)


def build_patch_from_dirs(*, base_dir: Path, work_dir: Path) -> PatchBuildResult:
    if _is_git_worktree(cwd=work_dir):
        return _build_patch_from_git_worktree(work_dir=work_dir)

    proc = subprocess.run(
        ["git", "diff", "--no-index", str(base_dir), str(work_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError(f"git diff failed: rc={proc.returncode}\n{_tail(proc.stderr)}")
    if not proc.stdout.strip():
        return PatchBuildResult(patch_text="", touched_paths=[])

    patch_text = _relativize_no_index_diff(
        patch_text=proc.stdout, base_dir=base_dir, work_dir=work_dir
    )
    patch_text = _filter_generated_artifacts_from_patch(patch_text)
    if not patch_text.strip():
        return PatchBuildResult(patch_text="", touched_paths=[])
    changes = parse_patch_changes(patch_text)
    touched = sorted({p for ch in changes for p in (ch.old_path, ch.new_path) if p is not None})
    return PatchBuildResult(patch_text=patch_text, touched_paths=touched)


def extract_git_diff(text: str) -> str:
    if "diff --git " not in text:
        raise ValueError("no git diff found in response")
    start = text.index("diff --git ")
    patch = text[start:].strip()

    if patch.endswith("```"):
        patch = patch[: patch.rfind("```")].rstrip()

    if "\n" not in patch and "\\n" in patch:
        try:
            patch = bytes(patch, "utf-8").decode("unicode_escape")
        except Exception:
            patch = (
                patch.replace("\\r\\n", "\n")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )

    patch = patch.rstrip() + "\n"
    return _canonicalize_unified_diff_paths(patch)


def extract_file_blocks(text: str) -> dict[str, str]:
    files: dict[str, list[str]] = {}
    cur_path: str | None = None
    cur_lines: list[str] = []

    def flush() -> None:
        nonlocal cur_path, cur_lines
        if cur_path is None:
            return
        files[cur_path] = list(cur_lines)
        cur_path = None
        cur_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("BEGIN_FILE "):
            flush()
            cur_path = _canonicalize_llm_patch_path(line.removeprefix("BEGIN_FILE ").strip())
            continue
        if line == "END_FILE":
            flush()
            continue
        if cur_path is not None:
            cur_lines.append(raw_line)

    flush()
    return {p: "\n".join(lines).rstrip() + "\n" for p, lines in files.items() if p}


def apply_file_blocks(*, files: dict[str, str], cwd: Path) -> list[str]:
    written: list[str] = []
    for rel_path, content in files.items():
        p = cwd / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written.append(rel_path)
    return sorted(set(written))


def write_command_results_json(
    path: Path, *, public: list[CommandResult], hidden: list[CommandResult]
) -> None:
    payload = {
        "public": [r.__dict__ for r in public],
        "hidden": [r.__dict__ for r in hidden],
    }
    write_text_atomic(path, stable_json_dumps(payload) + "\n")


class Sandbox:
    def __init__(self, *, run_dir: Path) -> None:
        self._run_dir = run_dir
        (self._run_dir / "sandboxes").mkdir(parents=True, exist_ok=True)

    def create(self, *, task_id: str, worker_id: str, round_id: int) -> Path:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = self._run_dir / "sandboxes" / f"r{round_id}_{task_id}_{worker_id}_{stamp}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def copy_workspace(self, *, workspace_dir: Path, sandbox_dir: Path) -> None:
        shutil.copytree(workspace_dir, sandbox_dir, dirs_exist_ok=True, ignore=copytree_ignore)

    def sync_back_diff(
        self,
        *,
        workspace_dir: Path,
        sandbox_dir: Path,
        changes: list[PatchFileChange],
    ) -> None:
        for change in changes:
            if change.kind in {"delete", "rename"} and change.old_path:
                if change.kind == "delete" or change.old_path != change.new_path:
                    target = workspace_dir / change.old_path
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                if change.kind == "delete":
                    continue

            if change.new_path:
                src = sandbox_dir / change.new_path
                dst = workspace_dir / change.new_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def sync_back_files(
        self,
        *,
        workspace_dir: Path,
        sandbox_dir: Path,
        paths: list[str],
    ) -> None:
        for p in paths:
            src = sandbox_dir / p
            dst = workspace_dir / p
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def artifact_for(
    path: Path, *, name: str, media_type: str | None = None, root: Path | None = None
) -> ArtifactRef:
    rel: str | None = None
    if root is not None:
        try:
            rel = str(path.relative_to(root))
        except Exception:
            rel = None
    return ArtifactRef(
        name=name,
        sha256=sha256_file(path) if path.exists() else None,
        path=rel or str(path),
        media_type=media_type,
    )
