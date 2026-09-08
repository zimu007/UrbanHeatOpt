#!/usr/bin/env python
"""C-role independent recomputation engine (Layer-1 operating + Layer-2 investment).

Reads ONLY archived result bytes (final_replay solutions) + archived case.json effective
parameters. Imports no model/competition code. Runs on any result root that has the
road_joint_v2 compact replay layout:  case.json, compact_pareto_frontiers.json, final_replay/.

Layer-1 (operating): gas/electricity energy, cost and carbon recomputed from raw
  dispatch_hourly.parquet + network_hourly.pump_kW_e x archived TOU prices / carbon factors.
Layer-2 (investment): device & fixed O&M from capacity_decisions.csv; pipe from
  network_decisions.csv built edges (length x unit_cost_CNY_per_pair_route_m x CRF,
  NO x2); station from station_fixed_capex x enabled count; connection from per-building
  connection capex x connected buildings.  All CRF from archived discount rate.

Checks vs pipeline exports (cost_breakdown.csv, frontier, independent_recalculation.json)
and vs frozen frontier values. Tolerances recorded per run.

Usage:
  python qa_engine.py <RESULT_ROOT> [--out-dir DIR] [--label NAME]
"""
from __future__ import annotations
import json, math, sys
from pathlib import Path
import argparse
import pandas as pd

TOL_BAL = 1e-6          # kW  heat-balance residual threshold (task doc)
REL_OK = 1e-5           # relative recompute target

def _hmap(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        try: out[int(k)] = float(v)
        except (ValueError, TypeError): pass
    return out

def crf(r: float, n: float) -> float:
    if n <= 0 or r <= 0: return 1.0
    return r * (1 + r) ** n / ((1 + r) ** n - 1)

def _find(where, built_val):
    """filter a frame's boolean-ish 'built'/'connected' column to a set of rows"""
    pass

def recompute_point(root: Path, pid: str, frontier_val: dict) -> dict:
    sol = root / "final_replay" / pid / "attempt_0001" / "solution"
    if not sol.is_dir():
        return {"point_id": pid, "error": "no solution dir"}
    cfg = json.loads((root / "case.json").read_text())
    eco = cfg["economics"]
    disc = float(eco["discount_rate"])
    cf_e = float(list(eco["electricity_carbon_kgCO2e_per_kWh_e"].values())[0])
    gas_price = float(list(eco["gas_price_CNY_per_kWh_LHV"].values())[0])
    gas_carb = float(list(eco["gas_carbon_kgCO2e_per_kWh_LHV"].values())[0])
    eprice = _hmap(eco["electricity_price_CNY_per_kWh_e"])
    eacarb = _hmap(eco["electricity_carbon_kgCO2e_per_kWh_e"])
    st_fix = float(eco["station_fixed_capex_CNY"]); st_life = float(list(eco["station_lifetime_years"].values())[0]) if isinstance(eco["station_lifetime_years"], dict) else float(eco["station_lifetime_years"])
    conn_map = {str(k): float(v) for k, v in eco["connection_capex_CNY"].items()}
    conn_life = float(list(eco["connection_lifetime_years"].values())[0]) if isinstance(eco["connection_lifetime_years"], dict) else float(eco["connection_lifetime_years"])
    techs = {t["technology_id"]: t for t in cfg["technologies"]}
    pipes = {p["pipe_type_id"]: p for p in cfg["pipes"]}

    # ---- Layer-1: operating cost & carbon ----
    disp = pd.read_parquet(sol / "dispatch_hourly.parquet")
    cap = pd.read_csv(sol / "capacity_decisions.csv")
    bal = pd.read_parquet(sol / "node_balance_check.parquet")
    nh_path = sol / "network_hourly.parquet"
    nh = pd.read_parquet(nh_path) if nh_path.exists() else None
    cost = pd.read_csv(sol / "cost_breakdown.csv").set_index("component")["annual_CNY"].to_dict()

    g = disp[disp["energy_carrier"] == "gas"]
    e = disp[disp["energy_carrier"] == "electricity"]
    gas_kwh = float(g["energy_input_kW"].sum())
    elec_by_hour = e.groupby(e["hour"].astype(int))["energy_input_kW"].sum()
    pump_by_hour = (nh.groupby(nh["hour"].astype(int))["pump_kW_e"].sum() if nh is not None and "pump_kW_e" in nh else pd.Series(dtype=float))
    all_elec = elec_by_hour.add(pump_by_hour, fill_value=0.0)
    hp_kwh = float(elec_by_hour.sum()); pump_kwh = float(pump_by_hour.sum()) if len(pump_by_hour) else 0.0
    elec_cost = float(sum(all_elec.get(h, 0.0) * eprice.get(h, 0.0) for h in all_elec.index))
    elec_carb = float(sum(all_elec.get(h, 0.0) * eacarb.get(h, cf_e) for h in all_elec.index))
    gas_cost = gas_kwh * gas_price
    gas_carb = gas_kwh * gas_carb

    # ---- Layer-2: investments ----
    inst = cap[cap["installed"] == 1]
    def _ann_tech(tech_id):
        rows = inst[inst["technology_id"] == tech_id]
        t = techs[tech_id]
        ann = float((rows["capacity_kW_th"] * rows["capex_CNY_per_kW_th"] * crf(disc, t["lifetime_years"])).sum())
        fom = float((rows["capacity_kW_th"] * rows["capex_CNY_per_kW_th"] * t["fixed_maintenance_fraction_per_year"]).sum())
        return ann, fom
    dev_annual = 0.0; fixed_om = 0.0
    for tid in inst["technology_id"].unique():
        a, f = _ann_tech(tid); dev_annual += a; fixed_om += f

    nd = pd.read_csv(sol / "network_decisions.csv")
    built = nd[nd["built"].astype(float).round().astype(int) == 1]
    # independent pipe annual = length x unit cost per pair-route-m x CRF(lifetime); NO x2 (unit already "pair")
    pipe_annual = float((built["length_m"] * built["unit_cost_CNY_per_pair_route_m"] *
                         built["lifetime_years"].map(lambda n: crf(disc, float(n)))).sum()) if len(built) else 0.0
    model_pipe_annual = float(built["annualized_investment_CNY"].sum()) if len(built) and "annualized_investment_CNY" in built else 0.0
    # gross check catches accidental x2: initial_investment should equal length x unit cost
    pipe_gross_check = float((built["length_m"] * built["unit_cost_CNY_per_pair_route_m"]).sum()) if len(built) else 0.0
    pipe_gross_model = float(built["initial_investment_CNY"].sum()) if len(built) and "initial_investment_CNY" in built else 0.0

    sd_path = sol / "station_decisions.csv"
    if sd_path.exists():
        sd = pd.read_csv(sd_path)
        n_st = int((sd["built"].astype(float).round().astype(int) == 1).sum())
    else:
        n_st = 0
    station_annual = n_st * st_fix * crf(disc, st_life)

    conn = pd.read_csv(sol / "building_connection.csv")
    conn_sel = conn[(conn["connected"].astype(float).round().astype(int) == 1)] if "connected" in conn else conn.iloc[0:0]
    conn_annual = float(sum(conn_map.get(str(b), 1000.0) for b in conn_sel["building_id"])) * crf(disc, conn_life)

    store = pd.read_csv(sol / "storage_decisions.csv") if (sol / "storage_decisions.csv").exists() else None
    storage_annual = 0.0

    total_recomp = dev_annual + fixed_om + pipe_annual + station_annual + conn_annual + storage_annual + elec_cost + gas_cost
    total_carbon = elec_carb + gas_carb

    # ---- reported references ----
    rep_cost = float(frontier_val["annual_real_cost_CNY_per_year"])
    rep_carb = float(frontier_val["annual_operating_carbon_kgCO2e_per_year"])
    rep_dev = cost.get("device_investment", 0.0); rep_fom = cost.get("fixed_om", 0.0)
    rep_pipe = cost.get("pipe_investment", 0.0); rep_st = cost.get("station_investment", 0.0)
    rep_conn = cost.get("connection_investment", 0.0); rep_elec = cost.get("electricity_cost", 0.0)
    rep_gas = cost.get("gas_cost", 0.0)
    rep_sum = float(sum(cost.values()))
    bal_max = float(bal["residual_kW"].abs().max()) if len(bal) else float("nan")
    bal_over = int((bal["residual_kW"].abs() > TOL_BAL).sum()) if len(bal) else -1

    def rel(a, b):
        return (a - b) / b if b else (0.0 if abs(a) == 0 else math.inf)

    return {
        "point_id": pid, "mode": frontier_val.get("mode"),
        "recomputed": {
            "gas_kWh_LHV": gas_kwh, "gas_cost_CNY": gas_cost, "gas_carbon_kg": gas_carb,
            "hp_elec_kWh": hp_kwh, "pump_elec_kWh": pump_kwh,
            "elec_cost_CNY": elec_cost, "elec_carbon_kg": elec_carb,
            "device_annual_CNY": dev_annual, "fixed_om_CNY": fixed_om,
            "pipe_annual_CNY": pipe_annual, "station_annual_CNY": station_annual,
            "connection_annual_CNY": conn_annual, "storage_annual_CNY": storage_annual,
            "total_annual_CNY": total_recomp, "total_carbon_kg": total_carbon,
            "n_stations": n_st, "n_connected": int(len(conn_sel)), "n_pipe_edges": int(len(built)),
            "pipe_gross_CNY": pipe_gross_check, "model_pipe_gross_CNY": pipe_gross_model,
        },
        "reported": {
            "real_cost_CNY": rep_cost, "carbon_kg": rep_carb,
            "breakdown_gas": rep_gas, "breakdown_elec": rep_elec,
            "breakdown_device": rep_dev, "breakdown_fixed_om": rep_fom,
            "breakdown_pipe": rep_pipe, "breakdown_station": rep_st,
            "breakdown_connection": rep_conn, "breakdown_sum": rep_sum,
            "model_pipe_annual_CNY": model_pipe_annual,
        },
        "checks": {
            "total_cost_rel": rel(total_recomp, rep_sum),
            "carbon_rel": rel(total_carbon, rep_carb),
            "gas_rel": rel(gas_cost, rep_gas), "elec_rel": rel(elec_cost, rep_elec),
            "device_rel": rel(dev_annual, rep_dev), "fixed_om_rel": rel(fixed_om, rep_fom),
            "pipe_rel": rel(pipe_annual, rep_pipe), "station_rel": rel(station_annual, rep_st),
            "connection_rel": rel(conn_annual, rep_conn),
            "pipe_model_self_consistency_rel": rel(pipe_annual, model_pipe_annual),
            "pipe_gross_model_rel": rel(pipe_gross_check, pipe_gross_model),
            "breakdown_sum_diff_CNY": rep_sum - rep_cost,
            "node_balance_max_abs_kW": bal_max, "node_balance_over_tol_count": bal_over,
        },
    }

def run(root: Path, out_dir: Path, label: str) -> dict:
    frontiers = json.loads((root / "compact_pareto_frontiers.json").read_text())
    by_point = {str(p.get("point_id")): p for p in frontiers["combined_frontier"] if p.get("point_id")}
    cfg = json.loads((root / "case.json").read_text())
    eco = cfg["economics"]
    points = []
    for replay in sorted((root / "final_replay").iterdir()):
        if not replay.is_dir(): continue
        pid = replay.name
        if pid not in by_point: continue
        points.append(recompute_point(root, pid, by_point[pid]))
    out = {
        "label": label, "method": "C_independent_recompute_layer1_plus_layer2",
        "source_root": str(root),
        "parameters": {"discount_rate": eco["discount_rate"],
                       "gas_price_CNY_per_kWh_LHV": float(list(eco["gas_price_CNY_per_kWh_LHV"].values())[0]),
                       "electricity_carbon_kgCO2e_per_kWh_e": float(list(eco["electricity_carbon_kgCO2e_per_kWh_e"].values())[0])},
        "tolerances": {"heat_balance_kW_max": TOL_BAL, "relative_target": REL_OK,
                       "note": "unit_cost_CNY_per_pair_route_m already includes both pipes; C does not x2"},
        "case_sha256": cfg.get("parameter_version"),
        "points_checked": len(points), "points": points,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{label}_qa.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out

def render_table(out: dict) -> str:
    L = []
    L.append(f"label={out['label']} | source={out['source_root']} | points={out['points_checked']}")
    L.append(f"{'point_id':28s}{'mode':9s}{'costΔ%':>8s}{'carbΔ%':>8s}{'gas%':>6s}{'elec%':>6s}"
             f"{'dev%':>6s}{'fom%':>6s}{'pipe%':>7s}{'st%':>6s}{'conn%':>7s}{'pipe_model%':>11s}{'bal_max':>9s}")
    for p in out["points"]:
        if "error" in p:
            L.append(f"{p['point_id']:28s} ERROR {p['error']}"); continue
        c = p["checks"]; r = p["recomputed"]
        L.append(f"{p['point_id']:28s}{str(p.get('mode')):9s}"
                 f"{100*c['total_cost_rel']:>8.4f}{100*c['carbon_rel']:>8.4f}{100*c['gas_rel']:>6.2f}{100*c['elec_rel']:>6.2f}"
                 f"{100*c['device_rel']:>6.2f}{100*c['fixed_om_rel']:>6.2f}{100*c['pipe_rel']:>7.3f}{100*c['station_rel']:>6.3f}{100*c['connection_rel']:>7.3f}"
                 f"{100*c['pipe_model_self_consistency_rel']:>11.3f}{c['node_balance_max_abs_kW']:>9.1e}")
    return "\n".join(L)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent / "out")
    ap.add_argument("--label", default=None)
    a = ap.parse_args()
    label = a.label or a.root.name
    out = run(a.root, a.out_dir, label)
    print(render_table(out))
    allok = all("error" not in p and
                abs(p["checks"]["total_cost_rel"]) < 1e-5 and abs(p["checks"]["carbon_rel"]) < 1e-5
                and abs(p["checks"]["pipe_rel"]) < 1e-3 and abs(p["checks"]["station_rel"]) < 1e-4
                and abs(p["checks"]["connection_rel"]) < 1e-4
                and abs(p["checks"]["pipe_model_self_consistency_rel"]) < 1e-5
                and p["checks"]["node_balance_max_abs_kW"] <= TOL_BAL
                for p in out["points"])
    print(f"\n判定: {'✅ 全部点 Layer-1+2 复算与报告一致' if allok else '⚠️ 有差异,见上'}")
    print(f"[写出] {a.out_dir / (label + '_qa.json')}")
    return 0 if allok else 2

if __name__ == "__main__":
    raise SystemExit(main())
