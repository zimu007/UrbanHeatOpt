#!/usr/bin/env python
"""C prep: per-source carbon decomposition (independent), all 8 R3 frontier points.

Matches B's planned export granularity: electricity carbon split into central-HP /
local-HP / network-pump, plus direct gas combustion carbon.  This is C's own
recompute (dispatch + network_hourly + archived factors), to cross-check B's future
carbon_breakdown.csv export.  kgCO2e; totals must equal reported operating carbon.

Usage: python carbon_decomposition.py [R3_ROOT]
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

R3 = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path("/home/Starture/Projects/cw/release_assets/R3_extracted/COMPACT_FULLSEASON_V1_20260903_R3")
OUT = Path(__file__).resolve().parent / "out"

cfg = json.loads((R3 / "case.json").read_text())
ecf = float(list(cfg["economics"]["electricity_carbon_kgCO2e_per_kWh_e"].values())[0])
gcf = float(list(cfg["economics"]["gas_carbon_kgCO2e_per_kWh_LHV"].values())[0])
F = json.loads((R3 / "compact_pareto_frontiers.json").read_text())
by_point = {p["point_id"]: p for p in F["combined_frontier"]}

rows = []
for pid in sorted(by_point):
    sol = R3 / "final_replay" / pid / "attempt_0001" / "solution"
    d = pd.read_parquet(sol / "dispatch_hourly.parquet")
    nh = pd.read_parquet(sol / "network_hourly.parquet")
    e = d[d["energy_carrier"] == "electricity"]
    ce = float(e[e.technology_id == "central_hp"]["energy_input_kW"].sum()) if "central_hp" in set(e.technology_id) else 0.0
    le = float(e[e.technology_id == "local_hp"]["energy_input_kW"].sum()) if "local_hp" in set(e.technology_id) else 0.0
    pu = float(nh["pump_kW_e"].sum())
    gas = float(d[d["energy_carrier"] == "gas"]["energy_input_kW"].sum())
    rows.append({
        "point_id": pid,
        "mode": by_point[pid].get("mode"),
        "centralHP_e_kWh": ce, "localHP_e_kWh": le, "pump_e_kWh": pu,
        "gas_LHV_kWh": gas,
        "centralHP_carbon_kgCO2e": ce * ecf, "localHP_carbon_kgCO2e": le * ecf,
        "pump_carbon_kgCO2e": pu * ecf, "gas_carbon_kgCO2e": gas * gcf,
    })

df = pd.DataFrame(rows)
df["elec_total_carbon_kgCO2e"] = (df.centralHP_carbon_kgCO2e + df.localHP_carbon_kgCO2e
                                   + df.pump_carbon_kgCO2e)
df["total_carbon_kgCO2e"] = df.elec_total_carbon_kgCO2e + df.gas_carbon_kgCO2e
df["reported_carbon_kgCO2e"] = [by_point[p]["annual_operating_carbon_kgCO2e_per_year"] for p in df.point_id]
df["rel_diff"] = (df.total_carbon_kgCO2e - df.reported_carbon_kgCO2e) / df.reported_carbon_kgCO2e
# tCO2e view
t = df.copy()
for c in ("centralHP_carbon_kgCO2e", "localHP_carbon_kgCO2e", "pump_carbon_kgCO2e",
          "gas_carbon_kgCO2e", "elec_total_carbon_kgCO2e", "total_carbon_kgCO2e",
          "reported_carbon_kgCO2e"):
    t[c] = (t[c] / 1000).round(3)
t.columns = [c.replace("_carbon_kgCO2e", "_tCO2e").replace("_kgCO2e", "_tCO2e").replace("_tCO2e_kgCO2e", "_tCO2e") for c in t.columns]
OUT.mkdir(parents=True, exist_ok=True)
t.to_csv(OUT / "carbon_decomposition_v1.csv", index=False)
print(t.to_string(index=False))
print("\nmax |相对差| vs reported:", f"{df.rel_diff.abs().max()*100:.5f}%  (应为 ~0,复算即对拍证据)")
