"""Render figures from saved V2 decisions, never invent geographical outputs."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd


def render_figures(root):
    root=Path(root)
    out=root/'figures'
    out.mkdir(exist_ok=False)
    case=json.loads((root/'case.json').read_text(encoding='utf-8'))
    frontiers=json.loads((root/'pareto_frontiers.json').read_text(encoding='utf-8'))
    qualification=f"V0 algorithm test | {len(case['buildings'])} buildings x {len(case['hours'])} h | NOT engineering design"
    fig,ax=plt.subplots(figsize=(9,5),layout='constrained')
    for mode,rows in frontiers['modes'].items():
        ax.plot([p['annual_operating_carbon_kgCO2e_per_year']/1000 for p in rows],
                [p['annual_real_cost_CNY_per_year'] for p in rows],marker='o',label=mode)
    ax.set(xlabel='Operating carbon (tCO2e per represented year)',ylabel='Real annualized cost (CNY)',title=qualification)
    ax.legend();ax.grid(alpha=.2)
    fig.savefig(out/'pareto.png',dpi=160);plt.close(fig)
    def solution(mode):
        marker=json.loads((root/'tasks'/f'{mode}-cost'/'success.json').read_text(encoding='utf-8'))
        file=next(p for p in marker['output_sha256'] if p.endswith('/solution/solution_summary.json'))
        return (root/file).parent
    modes=('central','distributed','hybrid')
    costs=pd.DataFrame({mode:pd.read_csv(solution(mode)/'cost_breakdown.csv').set_index('component').annual_CNY for mode in modes})
    fig,ax=plt.subplots(figsize=(10,6),layout='constrained')
    costs.T.plot.bar(stacked=True,ax=ax)
    ax.set(title=qualification,ylabel='Annualized CNY (unserved penalty excluded)')
    ax.legend(fontsize=7,loc='upper left',bbox_to_anchor=(1.0,1.0))
    fig.savefig(out/'cost_components.png',dpi=160);plt.close(fig)
    carbon={mode:json.loads((solution(mode)/'independent_recalculation.json').read_text(encoding='utf-8')) for mode in modes}
    fig,ax=plt.subplots(figsize=(8,5),layout='constrained')
    electric=[carbon[m]['carbon_electricity_kgCO2e']/1000 for m in modes]
    gas=[carbon[m]['carbon_gas_kgCO2e']/1000 for m in modes]
    ax.bar(modes,electric,label='Electricity indirect')
    ax.bar(modes,gas,bottom=electric,label='Gas combustion direct')
    ax.legend();ax.set(title=qualification,ylabel='Operating tCO2e per represented year',ylim=(0,max([a+b for a,b in zip(electric,gas)]+[1e-9])*1.15))
    fig.savefig(out/'carbon_components.png',dpi=160);plt.close(fig)
    selected=solution('central')
    pipes=pd.read_csv(selected/'network_decisions.csv').set_index('edge_id')
    sites=pd.read_csv(selected/'station_decisions.csv').set_index('site_id')
    fig,ax=plt.subplots(figsize=(9,7),layout='constrained')
    for e in case['network']['edges']:
        xs,ys=zip(*e['coordinates'])
        built=pipes.loc[e['edge_id'],'built']>.5
        ax.plot(xs,ys,color='#14866d' if built else '#a5adb4',linewidth=2 if built else .7,zorder=1)
    node_map={n['node_id']:n for n in case['network']['nodes']}
    for node in node_map.values():
        if node['node_type']=='building':
            ax.scatter(node['x_m'],node['y_m'],s=22,color='#1767a0',zorder=3)
            if len(case['buildings'])<=12:
                ax.annotate(node['node_id'],(node['x_m'],node['y_m']),xytext=(4,4),textcoords='offset points')
    for site in case['network']['sites']:
        n=node_map[site['attachment_node_id']]
        chosen=sites.loc[site['site_id'],'built']>.5
        ax.scatter(n['x_m'],n['y_m'],s=100,marker='*',color='#ef7b35' if chosen else '#929292',zorder=4)
    ax.set(title=qualification+'\nGreen: built atomic routes; gray: unbuilt; star: station',xlabel='Easting (m)',ylabel='Northing (m)')
    ax.set_aspect('equal',adjustable='datalim')
    fig.savefig(out/'network.png',dpi=160);plt.close(fig)
    dispatch=pd.read_parquet(selected/'dispatch_hourly.parquet')
    demand=pd.read_parquet(selected/'building_hourly.parquet').groupby('hour').demand_kW.sum()
    storage=pd.read_parquet(selected/'storage_hourly.parquet').groupby('hour')[['charge_kW','discharge_kW']].sum()
    loss=pd.read_parquet(selected/'network_hourly.parquet').groupby('hour').loss_kW_th.sum()
    output=dispatch.pivot_table(index='hour',columns='technology_id',values='heat_kW_th',aggfunc='sum')
    fig,ax=plt.subplots(figsize=(10,5),layout='constrained')
    output.plot(ax=ax)
    ax.plot(demand.index,demand,color='black',linestyle='--',label='Building demand')
    ax.plot(storage.index,storage.discharge_kW-storage.charge_kW,label='TES net output',color='#ae55a6')
    ax.set(title=qualification,xlabel='Model hour',ylabel='Thermal power (kW)');ax.legend()
    fig.savefig(out/'dispatch.png',dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,5),layout='constrained');ax.axis('off')
    produced=output.sum().sum();load=demand.sum();net_charge=(storage.charge_kW-storage.discharge_kW).sum();lost=loss.sum()
    ax.text(.05,.5,f'Device heat\n{produced:.3f} kWh_th',ha='center',va='center',transform=ax.transAxes,bbox=dict(boxstyle='round',fc='#d7eee8'))
    for y,label,number in [(.8,'Building heat',load),(.5,'Pipe loss',lost),(.2,'TES net charge incl. storage losses',net_charge)]:
        ax.annotate('',xy=(.68,y),xytext=(.2,.5),xycoords='axes fraction',arrowprops=dict(arrowstyle='->',lw=2))
        ax.text(.72,y,f'{label}\n{number:.3f} kWh_th',va='center',transform=ax.transAxes)
    ax.set_title(qualification+'\nIntegrated thermal energy flow; no hydraulic claim')
    fig.savefig(out/'energy_flow.png',dpi=160);plt.close(fig)
    validation={}
    for path in out.glob('*.png'):
        pixels=plt.imread(path)
        validation[path.name]=dict(width=int(pixels.shape[1]),height=int(pixels.shape[0]),nonblank=bool(pixels.std()>.01))
    (out/'render_validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
    if not all(x['nonblank'] for x in validation.values()):
        raise ValueError('输出图为空')
    return out
