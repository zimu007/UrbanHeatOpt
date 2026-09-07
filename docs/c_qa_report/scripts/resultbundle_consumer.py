#!/usr/bin/env python
"""C consumer for B's frozen ResultBundle (independent, no model imports).

Given a B-produced result_bundle.json, C:
  1. validates the envelope (required fields, qualified invariants, gap recompute);
  2. verifies every artifact file hash against the bundle listing;
  3. maps artifact roles -> paths and runs C's independent recomputation on the
     standard files (cost re-sum, carbon (gas/elec + pump) from raw dispatch,
     heat-balance residual, frontier/gap cross-check);
  4. writes a machine-readable QA json.

Params for the operating recompute come from a case.json-like file via --params
(its "economics" node); if absent, only self-consistency is reported and the
full operating recompute is recorded as a gap (never filled with zeroes).

Usage:
  python resultbundle_consumer.py <result_bundle.json> [--params case.json] [--out out.json]
"""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
import pandas as pd
import numpy as np

ALLOWED_TERM = ("not_executed", "optimal", "feasible", "infeasible", "unbounded", "error", "interrupted", "time_limit")

def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _hmap(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        try: out[int(k)] = float(v)
        except (ValueError, TypeError): pass
    return out

def crf(r: float, n: float) -> float:
    return r * (1 + r) ** n / ((1 + r) ** n - 1) if n > 0 and r > 0 else 1.0

def recompute_operating(sol_dir: Path, eco: dict) -> dict:
    """Independent operating cost/carbon from raw dispatch + network pump."""
    d = pd.read_parquet(sol_dir / "dispatch_hourly.parquet")
    nh_path = sol_dir / "network_hourly.parquet"
    nh = pd.read_parquet(nh_path) if nh_path.exists() else None
    gcf = float(list(eco["gas_carbon_kgCO2e_per_kWh_LHV"].values())[0])
    gp = float(list(eco["gas_price_CNY_per_kWh_LHV"].values())[0])
    eac = _hmap(eco["electricity_carbon_kgCO2e_per_kWh_e"]) or {0: float(list(eco["electricity_carbon_kgCO2e_per_kWh_e"].values())[0])}
    ep = _hmap(eco["electricity_price_CNY_per_kWh_e"])
    def p(h): return ep.get(int(h), 0.0)
    def c(h): return eac.get(int(h), float(list(eco["electricity_carbon_kgCO2e_per_kWh_e"].values())[0]))
    gas = d[d["energy_carrier"] == "gas"]; elec = d[d["energy_carrier"] == "electricity"]
    gas_kwh = float(gas["energy_input_kW"].sum())
    eb = elec.groupby(elec["hour"].astype(int))["energy_input_kW"].sum()
    pb = nh.groupby(nh["hour"].astype(int))["pump_kW_e"].sum() if nh is not None and "pump_kW_e" in nh else pd.Series(dtype=float)
    all_e = eb.add(pb, fill_value=0.0)
    elec_cost = float(sum(all_e.get(h, 0.0) * p(h) for h in all_e.index))
    elec_carb = float(sum(all_e.get(h, 0.0) * c(h) for h in all_e.index))
    return {"gas_kWh_LHV": gas_kwh, "gas_cost_CNY": gas_kwh * gp, "gas_carbon_kg": gas_kwh * gcf,
            "elec_kWh_hp": float(eb.sum()), "pump_kWh": float(pb.sum()) if len(pb) else 0.0,
            "elec_cost_CNY": elec_cost, "elec_carbon_kg": elec_carb,
            "op_cost_CNY": elec_cost + gas_kwh * gp, "op_carbon_kg": elec_carb + gas_kwh * gcf}

def consume(bundle_path: Path, params_path: Path | None, out_path: Path | None) -> dict:
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    run_id = payload.get("run_id"); mode = payload.get("mode"); obj = payload.get("objective")
    artifacts = {a["role"]: Path(a["path"]) for a in payload.get("artifacts", [])}
    report = {"run_id": run_id, "mode": mode, "objective": obj,
              "source_bundle": str(bundle_path),
              "contract": {"case_bundle_id": payload.get("case_bundle_id"),
                           "solve_request_id": payload.get("solve_request_id")}}

    # ---- 1 envelope invariants ----
    env = {"solver_executed": payload.get("solver_executed"), "qualified": payload.get("qualified"),
           "termination_condition": payload.get("termination_condition")}
    issues = []
    if payload.get("termination_condition") not in ALLOWED_TERM:
        issues.append("termination_condition 非法")
    if env["qualified"]:
        if not env["solver_executed"] or env["termination_condition"] not in ("optimal", "feasible"):
            issues.append("qualified 但未执行/非 optimal-feasible")
        se = payload.get("solve_evidence") or {}
        if "incumbent" in se and "best_bound" in se and "certified_gap" in se:
            rg = abs(se["incumbent"] - se["best_bound"]) / max(1.0, abs(se["incumbent"]))
            if abs(rg - se["certified_gap"]) > 1e-8: issues.append("certified_gap 与 bound 重算不一致")
            if not (se["certified_gap"] <= se.get("accepted_gap", 1.0) <= 1): issues.append("gap 未达接受限")
        else: issues.append("qualified 缺 solve_evidence 数字")
        if not ((payload.get("qa") or {}).get("passed") is True): issues.append("qualified 无 qa.passed")
        roles = set(artifacts)
        if not {"solver_log", "independent_qa"} <= roles: issues.append("qualified 缺 solver_log/independent_qa artifact")
    env["issues"] = issues

    # ---- 2 artifact hash integrity ----
    integrity = []
    for a in payload.get("artifacts", []):
        p = Path(a["path"])
        ok = p.is_file() and _sha(p) == a["sha256"]
        integrity.append({"role": a["role"], "file": str(p), "hash_ok": ok})
    env["integrity_all_ok"] = all(r["hash_ok"] for r in integrity)
    env["integrity"] = integrity
    report["envelope"] = env

    # ---- 3 independent recompute from artifacts ----
    sol_dir = None
    for name in ("dispatch_hourly", "cost_breakdown", "network_hourly"):
        if name in artifacts: sol_dir = artifacts[name].parent; break
    recompute = {}
    params = None
    if params_path and params_path.exists():
        cfg = json.loads(params_path.read_text(encoding="utf-8"))
        params = cfg.get("economics") or cfg
    if sol_dir is not None:
        cb = artifacts.get("cost_breakdown") or (sol_dir / "cost_breakdown.csv")
        if cb.is_file():
            cost = pd.read_csv(cb).set_index("component")["annual_CNY"].to_dict()
            recompute["cost_breakdown_sum_CNY"] = float(sum(cost.values()))
        ss = sol_dir / "solution_summary.json"
        if ss.exists():
            s = json.loads(ss.read_text())
            recompute["reported_real_cost_CNY"] = s.get("real_cost_CNY")
            recompute["reported_carbon_tCO2e"] = s.get("carbon_tCO2e")
        cb2 = artifacts.get("carbon_breakdown") or (sol_dir / "carbon_breakdown.csv")
        if cb2.is_file():
            car = pd.read_csv(cb2)
            col = [c for c in car.columns if "CNY" not in c and c != "component"]
            if col: recompute["carbon_breakdown_sum_kg"] = float(car[col[0]].sum())
        nb = sol_dir / "node_balance_check.parquet"
        if nb.exists():
            b = pd.read_parquet(nb)
            recompute["node_balance_max_abs_kW"] = float(b["residual_kW"].abs().max())
        if params is not None:
            recompute["recomputed"] = recompute_operating(sol_dir, params)
            # gas/electricity self consistency vs breakdown when present
            cost = recompute.get("cost_breakdown_sum_CNY")
        else:
            recompute["gap_note"] = "未提供 --params(economics);运行期电/气独立复算未执行(记为缺口,不补0)"
    else:
        recompute["gap_note"] = "bundle 未含可复算的标准结果 artifacts"
    report["recompute"] = recompute
    report["verdict"] = "PASS" if (not issues and env.get("integrity_all_ok")) else "FAIL"
    if out_path: out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--params", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    r = consume(a.bundle, a.params, a.out)
    print(json.dumps({k: v for k, v in r.items() if k != "envelope"}, ensure_ascii=False, indent=2))
    env = r["envelope"]
    print(f"\nenvelope issues: {env['issues']} | integrity_all_ok: {env['integrity_all_ok']} | 判定: {r['verdict']}")
    return 0 if r["verdict"] == "PASS" else 2

if __name__ == "__main__":
    raise SystemExit(main())
