from __future__ import annotations

import json
from pathlib import Path

import pytest

from urbanheatopt import cli
from urbanheatopt.qa import independent_runner


class _FakeBundle:
    bundle_id = "a" * 64

    def __init__(self, passed: bool = True):
        self.passed = passed

    def verify_artifacts(self, *, check_sources: bool = False):
        assert check_sources is True
        return {"passed": self.passed, "checked_files": 1, "checks": []}


class _FakeResult:
    bundle_id = "b" * 64

    def __init__(self, *, case_bundle_id: str = "a" * 64, passed: bool = True):
        self.case_bundle_id = case_bundle_id
        self.passed = passed

    def to_dict(self):
        return {
            "case_bundle_id": self.case_bundle_id,
            "qualified": True,
            "run_id": "formal_result",
        }

    def verify_artifacts(self):
        return {"passed": self.passed, "checked_files": 2, "checks": []}


def _arrange(monkeypatch, result_root: Path, *, bundle=None, result=None):
    bundle = bundle or _FakeBundle()
    result = result or _FakeResult()
    result_root.mkdir()
    monkeypatch.setattr(
        independent_runner,
        "load_prepared_road_case",
        lambda _path: (bundle, object(), {"road_case_rebuild_verified": True}),
    )
    monkeypatch.setattr(
        independent_runner.ResultBundle,
        "read",
        lambda _path: result,
    )
    monkeypatch.setattr(
        independent_runner,
        "audit_export",
        lambda _case, _root, *, audit_output: {
            "passed": True,
            "max_errors": {"heat_balance_kW": 0.0},
            "violations": [],
        },
    )


def test_standalone_qa_writes_new_evidence_directory(tmp_path, monkeypatch):
    result_root = tmp_path / "result"
    output = tmp_path / "qa"
    _arrange(monkeypatch, result_root)

    report = independent_runner.run_independent_qa(
        tmp_path / "case_bundle.json", result_root, output,
    )

    assert report["passed"] is True
    assert report["solver_executed_by_qa"] is False
    assert json.loads((output / "independent_qa.json").read_text(encoding="utf-8"))["passed"] is True
    assert json.loads((output / "qa_provenance.json").read_text(encoding="utf-8"))["result_bundle_id"] == "b" * 64


def test_standalone_qa_rejects_case_result_mismatch(tmp_path, monkeypatch):
    result_root = tmp_path / "result"
    _arrange(monkeypatch, result_root, result=_FakeResult(case_bundle_id="c" * 64))

    with pytest.raises(ValueError, match="CaseBundle.*不一致"):
        independent_runner.run_independent_qa(
            tmp_path / "case_bundle.json", result_root, tmp_path / "qa",
        )


@pytest.mark.parametrize("corrupt", ["case", "result"])
def test_standalone_qa_rejects_corrupt_frozen_artifacts(tmp_path, monkeypatch, corrupt):
    result_root = tmp_path / "result"
    _arrange(
        monkeypatch,
        result_root,
        bundle=_FakeBundle(passed=corrupt != "case"),
        result=_FakeResult(passed=corrupt != "result"),
    )

    with pytest.raises(ValueError, match="哈希验证失败"):
        independent_runner.run_independent_qa(
            tmp_path / "case_bundle.json", result_root, tmp_path / "qa",
        )


def test_standalone_qa_refuses_to_overwrite(tmp_path, monkeypatch):
    result_root = tmp_path / "result"
    output = tmp_path / "qa"
    output.mkdir()
    _arrange(monkeypatch, result_root)

    with pytest.raises(FileExistsError):
        independent_runner.run_independent_qa(
            tmp_path / "case_bundle.json", result_root, output,
        )


def test_qa_cli_requires_all_three_paths(capsys):
    assert cli.main(["qa"]) == 2
    assert "qa必须同时提供" in capsys.readouterr().err
