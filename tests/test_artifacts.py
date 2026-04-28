from __future__ import annotations

import os
import time
from pathlib import Path

from codex_thread_bridge.artifacts import ArtifactService
from codex_thread_bridge.config import BridgeConfig


def test_detects_allowed_file_created_after_run_start(tmp_path: Path) -> None:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    report_path = export_dir / "report.md"

    run_started_at = time.time()
    time.sleep(0.01)
    report_path.write_text("# Report\n", encoding="utf-8")

    service = ArtifactService(config)
    candidates = service.detect("Saved to %s" % report_path, run_started_at)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.path == report_path.resolve()
    assert candidate.status == "allowed"
    assert candidate.reason == "within allowed artifact roots"
    assert candidate.mime_type == "application/octet-stream"
    assert candidate.size_bytes == report_path.stat().st_size


def test_detects_allowed_quoted_file_with_spaces(tmp_path: Path) -> None:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    report_path = export_dir / "my report.md"

    run_started_at = time.time()
    time.sleep(0.01)
    report_path.write_text("# Report\n", encoding="utf-8")

    service = ArtifactService(config)
    candidates = service.detect('Created "%s".' % report_path, run_started_at)

    assert len(candidates) == 1
    assert candidates[0].path == report_path.resolve()
    assert candidates[0].status == "allowed"


def test_detects_allowed_unquoted_file_with_spaces_conservatively(tmp_path: Path) -> None:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    report_path = export_dir / "my report.md"

    run_started_at = time.time()
    time.sleep(0.01)
    report_path.write_text("# Report\n", encoding="utf-8")

    service = ArtifactService(config)
    candidates = service.detect("Created %s successfully." % report_path, run_started_at)

    assert len(candidates) == 1
    assert candidates[0].path == report_path.resolve()
    assert candidates[0].status == "allowed"


def test_blocks_sensitive_and_old_paths(tmp_path: Path) -> None:
    config = BridgeConfig(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "data" / "bridge.sqlite3",
        attachments_dir=tmp_path / "data" / "attachments",
        artifact_roots=(tmp_path,),
        owner_user_ids=frozenset({"owner-1"}),
        group_qa_cwd=tmp_path,
        sensitive_path_markers=(".ssh",),
    )
    old_path = tmp_path / "exports" / "old.md"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    old_mtime = time.time() - 10
    os.utime(old_path, (old_mtime, old_mtime))

    sensitive_path = tmp_path / ".ssh" / "secret.txt"
    sensitive_path.parent.mkdir(parents=True, exist_ok=True)
    sensitive_path.write_text("secret", encoding="utf-8")

    service = ArtifactService(config)
    text = "Old: %s Sensitive: %s" % (old_path, sensitive_path)
    candidates = service.detect(text, time.time())

    assert len(candidates) == 2
    by_path = {candidate.path: candidate for candidate in candidates}
    assert by_path[old_path.resolve()].status == "blocked"
    assert by_path[old_path.resolve()].reason == "file predates run start"
    assert by_path[sensitive_path.resolve()].status == "blocked"
    assert by_path[sensitive_path.resolve()].reason == "path contains sensitive marker"


def test_blocks_file_exceeding_max_artifact_bytes(tmp_path: Path) -> None:
    config = BridgeConfig(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "data" / "bridge.sqlite3",
        attachments_dir=tmp_path / "data" / "attachments",
        artifact_roots=(tmp_path / "exports",),
        owner_user_ids=frozenset({"owner-1"}),
        group_qa_cwd=tmp_path,
        max_artifact_bytes=4,
    )
    report_path = tmp_path / "exports" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    run_started_at = time.time()
    time.sleep(0.01)
    report_path.write_text("12345", encoding="utf-8")

    service = ArtifactService(config)
    candidates = service.detect("Saved to %s" % report_path, run_started_at)

    assert len(candidates) == 1
    assert candidates[0].path == report_path.resolve()
    assert candidates[0].status == "blocked"
    assert candidates[0].reason == "file exceeds size limit"


def test_blocks_existing_file_outside_artifact_roots(tmp_path: Path) -> None:
    config = BridgeConfig.local_dev(tmp_path, {"owner-1"})
    outside_path = tmp_path / "outside" / "report.md"
    outside_path.parent.mkdir(parents=True, exist_ok=True)

    run_started_at = time.time()
    time.sleep(0.01)
    outside_path.write_text("# Report\n", encoding="utf-8")

    service = ArtifactService(config)
    candidates = service.detect("Saved to %s" % outside_path, run_started_at)

    assert len(candidates) == 1
    assert candidates[0].path == outside_path.resolve()
    assert candidates[0].status == "blocked"
    assert candidates[0].reason == "outside allowed artifact roots"
