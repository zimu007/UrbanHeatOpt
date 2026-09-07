"""Focused contract tests for the production RoadCase builder boundary."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from urbanheatopt.data.bundles import CaseBundle, INTERFACE_VERSION
from urbanheatopt.data.road_builder import _network_from_artifacts


def _bundle(path: Path) -> CaseBundle:
    digest = "a" * 64
    return CaseBundle.from_dict({
        "interface_version": INTERFACE_VERSION,
        "data_version": "test",
        "parameter_version": "parameter",
        "git_sha": "git",
        "artifacts": [{"role": "loads", "path": str(path.resolve()), "sha256": digest}],
        "source_hashes": {str((path.parent / "source.csv").resolve()): "b" * 64},
        "units": {"heating_kW": "kW_th"},
        "capabilities_required": [],
        "status": {"input_valid": True, "parameter_valid": True,
                   "canonical_valid": True, "snapshot_complete": True},
        "physical_scope": {"buildings": 62, "hours": 2160},
        "economic_boundary": {"scenario": "base"},
        "generated_spatial_status": {"network_sha256": "c" * 64},
    })


def test_case_bundle_content_id_ignores_fresh_output_path(tmp_path) -> None:
    left = _bundle(tmp_path / "run_a" / "loads.parquet")
    right = _bundle(tmp_path / "run_b" / "loads.parquet")
    assert left.bundle_id != right.bundle_id
    assert left.content_id == right.content_id


def test_network_product_hash_is_recomputed_not_trusted(tmp_path) -> None:
    network = {"contract_version": "test", "nodes": [], "edges": [], "sites": []}
    network["network_sha256"] = sha256(json.dumps(network, sort_keys=True).encode()).hexdigest()
    network_path = tmp_path / "road_network.json"
    manifest_path = tmp_path / "network_manifest.json"
    network_path.write_text(json.dumps(network), encoding="utf-8")
    manifest_path.write_text(json.dumps({
        "schema": "urbanheatopt_network_product_1.0.0",
        "network_sha256": network["network_sha256"],
    }), encoding="utf-8")
    loaded, _ = _network_from_artifacts({
        "road_network": network_path, "network_manifest": manifest_path,
    })
    assert loaded["network_sha256"] == network["network_sha256"]

    network["nodes"].append({"node_id": "tampered"})
    network_path.write_text(json.dumps(network), encoding="utf-8")
    with pytest.raises(ValueError, match="network_sha256"):
        _network_from_artifacts({"road_network": network_path, "network_manifest": manifest_path})
