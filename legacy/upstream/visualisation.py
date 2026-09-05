
import os

import numpy as np
import pandas as pd
import geopandas as gpd
import contextily as ctx
import matplotlib.pyplot as plt
from shapely.geometry.linestring import LineString
from shapely import wkt
from termcolor import cprint
from matplotlib.lines import Line2D
import calendar
import matplotlib.dates as mdates
import datetime
import locale

from legacy.upstream.data import *
from matplotlib.table import Table




def extract_node_centroids(model_input_data: dict) -> dict:
    """Provides a dictionary with node names as keys and centroids as values.

    :param model_input_data: dictionary with the input data of the model
    :type model_input_data: dict
    :return: dictionary with node names as keys and centroids as values
    :rtype: dict
    """

    # extract the centroids of the nodes from the input data
    gdf_heat_nodes = model_input_data['heat_nodes']
    gdf_heat_nodes.crs = 'EPSG:3857'
    # change crs
    gdf_heat_nodes = gdf_heat_nodes.to_crs('EPSG:4326')

    # creat a dictionary with node names as keys and centroids as values
    node_centroids = {}
    for index, row in gdf_heat_nodes.iterrows():
        node_centroids[row['cluster_id']] = row['geometry']

    cprint("Done: extracted node centroids", 'green')

    return node_centroids

def extract_node_shapes(model_input_data: dict) -> dict:
    """provides a dictionary with node names as keys and shapes of heat node areas as values.

    :param model_input_data: dictionary with the input data of the model
    :type model_input_data: dict
    :return: dictionary with node names as keys and shapes of heat node areas as values
    :rtype: dict
    """

    # extract the shapes of the nodes from the input data
    gdf_heat_nodes = model_input_data['heat_nodes']
    gdf_heat_nodes.crs = 'EPSG:3857'

    # change crs
    gdf_heat_nodes = gdf_heat_nodes.to_crs('EPSG:4326')

    # creat a dictionary with node names as keys and centroids as values
    node_shapes = {}
    for index, row in gdf_heat_nodes.iterrows():
        node_shapes[row['cluster_id']] = wkt.loads(row['convex_hull'])

    # change the crs of the shapes
    node_shapes_gdf = gpd.GeoDataFrame(geometry=list(node_shapes.values()), crs='EPSG:3857')
    node_shapes_gdf = node_shapes_gdf.to_crs('EPSG:4326')
    node_shapes = dict(zip(node_shapes.keys(), node_shapes_gdf.geometry))

    cprint("Done: extracted node shapes", 'green')

    return node_shapes



def plot_investment_decisions(model_input_data: dict, model_output_data: dict, dict_nodes: dict, dict_shapes: dict, figure_path: str):
    """Generates a plot of the investment decisions made by the model on a geospatial map.
    
    :param model_input_data: input data of the model, including heat network and heat generation units
    :type model_input_data: dict
    :param model_output_data: output data of the model, including investment decisions and mass flow
    :type model_output_data: dict
    :param dict_nodes: dictionary with node names as keys and centroids as values
    :type dict_nodes: dict
    :param dict_shapes: dictionary with node names as keys and shapes of heat node areas as values
    :type dict_shapes: dict
    :param figure_path: path to the directory where the figure should be saved
    :type figure_path: str
    """

    ## shapes of the clusters
    # extract the investment decisions from the output data
    df_DHconnect = model_output_data['vDHconnect'].copy()
    df_DHconnect.rename(columns={'index_set_1': 'node_id'}, inplace=True)
    # set node as index
    df_DHconnect.set_index('node_id', inplace=True)

    # merge the coordinates to the nodes
    df_DHconnect['geometry'] = df_DHconnect.index.map(dict_shapes)

    gdf_DHconnect = gpd.GeoDataFrame(df_DHconnect, geometry=df_DHconnect['geometry'])

    ## pipes 
    # extract the investment decisions from the output data
    df_BuildPipe = model_output_data['vBinBuildPipe'].copy()
    df_BuildPipe.rename(columns={'value': 'invest'}, inplace=True)
    df_PipeMFInf = model_output_data['vPipeMassFlowInv'].copy()
    df_PipeMFInf.rename(columns={'value': 'mass_flow'}, inplace=True)
    # merge the mass flow to the investment decisions by the index set 1 and index set 2
    df_BuildPipe = df_BuildPipe.merge(df_PipeMFInf, on=['index_set_1', 'index_set_2'], how='left')

    #rename columns    
    df_BuildPipe.rename(columns={'index_set_1': 'node_from', 'index_set_2': 'node_to'}, inplace=True)

    # match the from the to nodes with the coordinates and create linestrings
    df_BuildPipe['from_geometry'] = df_BuildPipe['node_from'].map(dict_nodes)
    df_BuildPipe['to_geometry'] = df_BuildPipe['node_to'].map(dict_nodes)

    # create linestrings
    df_BuildPipe['geometry'] = df_BuildPipe.apply(lambda x: LineString([x['from_geometry'], x['to_geometry']]), axis=1)

    # create a geodataframe with the investment decisions
    gdf_BuildPipe = gpd.GeoDataFrame(df_BuildPipe, geometry=df_BuildPipe['geometry'])

    # load the pipe candidates
    gdf_pipe_candidates = model_input_data['heat_network'].copy()
    gdf_pipe_candidates.crs = 'EPSG:3857'
    # change crs
    gdf_pipe_candidates = gdf_pipe_candidates.to_crs('EPSG:4326')   


    ## plot the investment decisions
    fig, ax = plt.subplots(figsize=(6, 5))

    # add a title
    plt.title('Investment decisions', fontsize=12)

    # plot the nodes
    for node_id, centroid in dict_nodes.items():
        plt.plot(centroid.x, centroid.y, 'grey', marker='o', markersize=3, zorder=4)


    gdf_DHconnect[gdf_DHconnect['value'] == 1].plot(ax=ax, color='bisque', edgecolor='k', markersize=100, alpha=0.75, zorder=1)
    gdf_DHconnect[gdf_DHconnect['value'] == 0].plot(ax=ax, color='grey', edgecolor='k', markersize=100, alpha=0.2, zorder=1)

    #plot pipe candidates
    gdf_pipe_candidates.plot(ax=ax, color='grey', alpha=1, linewidth=1, zorder=2)

    linewidth_scale = 0.016

    # plot the pipes
    gdf_BuildPipe.plot(ax=ax, color='crimson', linewidth=linewidth_scale*gdf_BuildPipe['mass_flow'], zorder=3)


    # add a textlable below the legend
    height = 0.92
    ax.text(0.80, height, '500 m³/h', horizontalalignment='center', verticalalignment='center', transform=ax.transAxes, fontsize=10)
    ax.plot([0.91, 0.96], [height, height], 'r-', linewidth=500*linewidth_scale, transform=ax.transAxes)


    # add a maker for the heat generation units
    gdf_heat_gen_units = model_input_data['heat_gen_units'].copy()
    gdf_heat_gen_units.crs = 'EPSG:3857'
    # change crs
    gdf_heat_gen_units = gdf_heat_gen_units.to_crs('EPSG:4326')

    #load the investment decisions
    df_heat_investment = model_output_data['vCentralHeatProdInv'].copy()
    df_heat_investment.rename(columns={'index_set_1': 'unit', 'value': 'power_investment'}, inplace=True)

    # merge the investment decisions to the heat generation units
    gdf_heat_gen_units = gdf_heat_gen_units.merge(df_heat_investment, left_on='unit', right_on='unit', how='left')

    # distinguish between the types of heat generation units
    gdf_heat_gen_units['alpha'] = np.where(gdf_heat_gen_units['power_investment'] > 0.0, 0.9, 0.3)


    #gdf_heat_gen_units[gdf_heat_gen_units['isWH']==1].plot(ax=ax, color='darkgreen', marker='s', edgecolor='k', markersize=100, alpha=gdf_heat_gen_units.loc[gdf_heat_gen_units['isWH']==1,'alpha'], zorder=5)
    #gdf_heat_gen_units[gdf_heat_gen_units['isBoiler']==1].plot(ax=ax, color='gold', marker='s', edgecolor='k', markersize=100, alpha=gdf_heat_gen_units.loc[gdf_heat_gen_units['isBoiler']==1,'alpha'], zorder=5)
    #gdf_heat_gen_units[gdf_heat_gen_units['isTES']==1].plot(ax=ax, color='slateblue', marker='s', edgecolor='k', markersize=100, alpha=gdf_heat_gen_units.loc[gdf_heat_gen_units['isTES']==1,'alpha'], zorder=5)
    
    # enumerate the labels for invested heat generation units and collect info for the table
    invested_units = []
    for idx, (index, row) in enumerate(gdf_heat_gen_units.iterrows(), 1):
        # Determine background color for the id cell based on unit type
        if row['isWH'] == 1:
            id_bgcolor = 'darkgreen'
        elif row['isBoiler'] == 1:
            id_bgcolor = 'gold'
        elif row['isTES'] == 1:
            id_bgcolor = 'slateblue'
        else:
            id_bgcolor = 'grey'
        # Set alpha depending on investment
        box_alpha = 0.8 if row['power_investment'] > 0 else 0.2
        plt.text(
            row['geometry'].x, row['geometry'].y, f"{idx}", fontsize=8, color='white',
            ha='center', va='center',
            bbox=dict(facecolor=id_bgcolor, alpha=box_alpha, edgecolor='black'), zorder=6
        )
        invested_units.append((idx, row['unit'], row['power_investment'] / 1e3, id_bgcolor, box_alpha))

    if invested_units:
        # Prepare table data with just two columns: id and Investment
        table_data = [["id", "Investment"]]
        for idx, unit, mw, id_bgcolor, box_alpha in invested_units:
            table_data.append([idx, f"{mw:.1f} MW"])
        # Calculate the height of the table based on the number of rows
        row_height = 0.07
        table_height = row_height * len(table_data)
        # Place the table aligned with the right border and bottom line of the axes
        table = plt.table(
            cellText=table_data,
            colLabels=None,
            cellLoc='center',
            loc='bottom',
            bbox=[1.01, 0.0, 0.35, table_height]
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        # Set background color and alpha for id cells (first column, skip header)
        for i, (_, _, _, id_bgcolor, box_alpha) in enumerate(invested_units, start=1):
            table[(i, 0)].set_facecolor(id_bgcolor)
            table[(i, 0)].set_alpha(box_alpha)
            table[(i, 0)].get_text().set_color('white')



    # add a legend with marker symbols

    custom_lines = [Line2D([0], [0], color='bisque', marker='o', markeredgecolor='k', markersize=8, linestyle='None'),
                    Line2D([0], [0], color='grey', marker='o', markeredgecolor='k', markersize=8, linestyle='None'),
                    Line2D([0], [0], color='darkgreen', marker='s', markeredgecolor='k', markersize=8, linestyle='None'),
                    Line2D([0], [0], color='gold', marker='s', markeredgecolor='k', markersize=8, linestyle='None'),
                    Line2D([0], [0], color='slateblue', marker='s', markeredgecolor='k', markersize=8, linestyle='None'),                    Line2D([0], [0], color='red', lw=4)]
    ax.legend(custom_lines, ['Connected', 'Not connected', 'Waste Heat Unit', 'Boiler', 'TES', 'Pipe'], fontsize=10, loc='upper left', bbox_to_anchor=(1, 1), frameon=True)
    
    # make x and y axis equal
    ax.set_aspect('equal', adjustable='box')

    # add a basemap
    ctx.add_basemap(ax, crs='EPSG:4326', source=ctx.providers.CartoDB.Positron)

    # show the plot
    plt.show()

    # check if the figure path exists
    if not os.path.exists(figure_path):
        os.makedirs(figure_path)

    # save the plot as pdf
    fig.savefig(os.path.join(figure_path, 'investment_decisions.pdf'), bbox_inches='tight')
    
    cprint("Done: saved investment decisions plot to disk", 'green')



def merge_time_series(model_input_data: dict, model_output_data: dict) -> pd.DataFrame:
    """Merge all time series data from the model input and output data into a single dataframe.

    :param model_input_data: input data of the model, including heat demand and waste heat profiles
    :type model_input_data: dict
    :param model_output_data: output data of the model, including local heat production and central heat production
    :type model_output_data: dict
    :return: a dataframe with the energy balance of the local heat production, central heat production, waste heat from heat generation units and heat demand
    :rtype: pd.DataFrame
    """

    df_heat_demand = model_input_data['heat_demand'].copy()

    # calc the sum over the heat nodes (Columns) excluding the 'hour' column
    df_heat_demand['heat_demand'] = df_heat_demand.drop(columns=['hour']).sum(axis=1)

    df_local_heat_production = model_output_data['vLocalHeatProd'].copy()
    df_local_heat_production.rename(columns={'index_set_1': 'node', 'index_set_2': 'hour', 'value': 'local_heat_gen'}, inplace=True)
    # regroup by the same hour and sum over all heat nodes
    df_local_heat_production = df_local_heat_production.groupby('hour').sum()
    df_local_heat_production.drop(columns='node', inplace=True)

    df_central_heat_production = model_output_data['vCentralHeatProd'].copy()
    df_central_heat_production.rename(columns={'index_set_1': 'unit', 'index_set_2': 'hour', 'value': 'power'}, inplace=True)
    # reshape and make the units to columns
    df_central_heat_production = df_central_heat_production.pivot(index='hour', columns='unit', values='power')
    #rename all columns with a prefix 'gen_'
    df_central_heat_production.rename(columns={col: f'gen_{col}' for col in df_central_heat_production.columns}, inplace=True)

    # load the potential waste heat from the heat generation units
    df_heat_gen_units = model_input_data['waste_heat_prof'].copy()
    # recalc all columns to kW
    df_heat_gen_units = df_heat_gen_units * 1.16389 * (model_input_data['parameter_cost']['pTsupply'] -  model_input_data['parameter_cost']['pTreturn'])
    
    #rename all columns with a prefix 'wh_'
    df_heat_gen_units.rename(columns={col: f'wh_{col}' for col in df_heat_gen_units.columns}, inplace=True)

    # merge all dataframes togehther by hour
    df_energy_balance = df_local_heat_production.merge(df_heat_demand.loc[:,'heat_demand'], left_index=True, right_index=True)
    df_energy_balance = df_energy_balance.merge(df_central_heat_production, left_index=True, right_index=True)
    df_energy_balance = df_energy_balance.merge(df_heat_gen_units, left_index=True, right_index=True)
    df_energy_balance.drop(columns='wh_hour', inplace=True)


    # the index now iterates the hours, generate a time index out of it sarting with 2019-01-01 00:00:00
    df_energy_balance.index = pd.date_range(start='2019-01-01 00:00:00', periods=len(df_energy_balance), freq='h')
    cprint("Done: saved time series plot to disk", 'green')

    return df_energy_balance

def plot_energy_balance(df_energy_balance: pd.DataFrame, figure_path: str):
    """Plot the annual energy balance of the local heat production, central heat production, waste heat from heat generation units and heat demand as a pie chart.

    :param df_energy_balance: dataframe with the energy balance of the local heat production, central heat production, waste heat from heat generation units and heat demand
    :type df_energy_balance: pd.DataFrame
    :param figure_path: path to the directory where the figure should be saved
    :type figure_path: str
    """

    df_yearly_sums = df_energy_balance.resample('YE').sum()

    # make one pie chart plot of the yearly sums of 'local_heat_gen' and all 'gen_' columns 
    fig, ax = plt.subplots(figsize=(6, 5))
    # get the columns of the dataframe that start with 'gen_'
    gen_columns = [col for col in df_yearly_sums.columns if col.startswith('gen_')]
    # add the local heat generation to the list of columns
    gen_columns.append('local_heat_gen')
    # calculate the sum of the columns
    gen_sums = df_yearly_sums[gen_columns].sum()
    # put the local heat generation at the first position
    gen_sums = gen_sums.reindex(['local_heat_gen'] + [col for col in gen_sums.index if col != 'local_heat_gen'])
    # plot a pie chart of the sums
    labels = [col.replace('gen_', '') if col.startswith('gen_') else 'Decentral Heat Production' for col in gen_sums.index]
    palette = plt.get_cmap('tab10')
    n_gens = len(gen_columns)
    colors = ['dimgrey'] + [palette(i % 10) for i in range(n_gens)]

    def absolute_autopct(values):
        def my_autopct(pct):
            total = sum(values)
            val = int(round(pct * total / 100.0 * 1e-6))
            return f'{val:,} GWh'
        return my_autopct
    
    wedges, texts, autotexts = ax.pie(
            gen_sums,
            autopct=absolute_autopct(gen_sums),
            startangle=90,
            colors=colors,
            wedgeprops={'edgecolor': 'black'},
            textprops={'fontsize': 10}
        )
        # Set white box with alpha 0.8 for each label and autopct
    for t in texts + autotexts:
        t.set_bbox(dict(facecolor='white', alpha=0.7, edgecolor='none'))


    plt.title('Annual energy balance', fontsize=12)
    ax.legend(labels=labels, bbox_to_anchor=(1, 0.75), fontsize=10)
    plt.show()


    # save the plot as pdf
    fig.savefig(os.path.join(figure_path, 'energy_balance.pdf'), bbox_inches='tight')



def plot_time_resolved(df_energy_balance: pd.DataFrame, figure_path: str, time_invervall='W', start_hour: int = None, duration_hours: int = None):
    """Generates a time resolved plot of the energy balance of the local heat production, central heat production, waste heat from heat generation units and heat demand.

    This function resamples the input dataframe to the specified time interval and plots the energy balance as a stackplot.
    The x-axis is adjusted to show the time interval, and the plot is saved as a PDF file.

    :param df_energy_balance: dataframe with the energy balance of the local heat production, central heat production, waste heat from heat generation units and heat demand
    :type df_energy_balance: pd.DataFrame
    :param figure_path: path to the directory where the figure should be saved 
    :type figure_path: str
    :param time_invervall: Sets the time interval for resampling the data (i.e. 'H' for hourly), defaults to 'W' (weekly)
    :type time_invervall: str, optional
    :param start_hour: index of the starting our of the range in the plot time axis, defaults to None
    :type start_hour: int, optional
    :param duration_hours: duration of time steps to plot in the time axis, defaults to None
    :type duration_hours: int, optional
    """

    # Set global locale to English (may vary by system)
    try:
        locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')  # Works on Unix
    except locale.Error:
        locale.setlocale(locale.LC_TIME, 'C')  # Fallback: default C locale uses English

    # resmaple 
    loc_df_energy_balance = df_energy_balance.resample(time_invervall).sum()/1e3

    # make a plot
    fig, ax = plt.subplots(figsize=(6, 4))

    # plot the heat demand, shift x axis by 1
    ax.plot(loc_df_energy_balance.index + pd.Timedelta(hours=1), loc_df_energy_balance['heat_demand'], color='black', linewidth=1, label='Heat Demand')
    
    # get the columns of the dataframe that start with 'gen_'
    gen_columns = [col for col in loc_df_energy_balance.columns if col.startswith('gen_')]
    # Define a color palette for the stackplot
    # Use tab10 for up to 10 colors
    palette = plt.get_cmap('tab10')
    n_gens = len(gen_columns)
    colors =  [palette(i % 10) for i in range(n_gens)] + ['dimgrey']

    ax.stackplot(
        loc_df_energy_balance.index,
        *loc_df_energy_balance.loc[:, gen_columns + ['local_heat_gen']].values.T,
        labels=[col.replace('gen_', '') for col in gen_columns] + ['Decentral Heat Production'],
        colors=colors
    )
    # add a legend outside the plot, reverse order except leave the first entry in first position
    handles, labels = ax.get_legend_handles_labels()
    if len(handles) > 1:
        ax.legend([handles[0]] + handles[:0:-1], [labels[0]] + labels[:0:-1], bbox_to_anchor=(1.02, 0.5), loc='center left', fontsize=10)
    else:
        ax.legend(handles, labels, bbox_to_anchor=(1.02, 0.5), loc='center left', fontsize=10)

    # add a title
    plt.title('Time resolved energy balance' + '\n' + f'Time intervall = {time_invervall}', fontsize=12)

    # add a grid
    plt.grid(True, linestyle='--', alpha=0.5)

    # add x and y labels
    #plt.xlabel('Time')
    plt.ylabel(f'Power in MWh / {time_invervall}', fontsize=10, fontweight='bold')

    # Set x-axis ticks and labels based on the time interval and duration
    if time_invervall in ['M', 'MS', 'ME', 'BM', 'CBM', 'SM', 'SMS']:
        # Monthly intervals
        start = loc_df_energy_balance.index.min()
        end = loc_df_energy_balance.index.max()
        months = pd.date_range(start=start, end=end, freq='MS')
        month_labels = [calendar.month_abbr[dt.month] for dt in months]
        ax.set_xticks(months)
        ax.set_xticklabels(month_labels)
    elif time_invervall in ['W', 'W-MON', 'W-SUN', 'W-TUE', 'W-WED', 'W-THU', 'W-FRI', 'W-SAT']:
        # Weekly intervals
        weeks = loc_df_energy_balance.index
        week_labels = [dt.strftime('%b %d') for dt in weeks]
        ax.set_xticks(weeks)
        ax.set_xticklabels(week_labels, rotation=45, ha='right')
    elif time_invervall in ['D', 'B']:
        # Daily intervals
        days = loc_df_energy_balance.index
        day_labels = [dt.strftime('%b %d') for dt in days]
        ax.set_xticks(days)
        ax.set_xticklabels(day_labels, rotation=45, ha='right')
    elif time_invervall in ['H', 'h', 'T', 'S']:
        # Hourly or shorter intervals
        # Show one label for each day
        days = pd.date_range(start=loc_df_energy_balance.index.min(), end=loc_df_energy_balance.index.max(), freq='D')
        ticks = [loc_df_energy_balance.index.get_loc(day) if day in loc_df_energy_balance.index else None for day in days]
        # Remove None values (in case some days are missing)
        ticks = [loc_df_energy_balance.index[i] for i in ticks if i is not None]
        tick_labels = [dt.strftime('%b %d') for dt in ticks]
        ax.set_xticks(ticks)
        ax.set_xticklabels(tick_labels, rotation=45, ha='right')
    else:
        # Default: show every Nth tick for clarity
        ticks = loc_df_energy_balance.index[::max(1, len(loc_df_energy_balance)//12)]
        tick_labels = [dt.strftime('%b %d') for dt in ticks]
        ax.set_xticks(ticks)
        ax.set_xticklabels(tick_labels, rotation=45, ha='right')


    # Set x limits based on start_hour and duration_hours parameters
    if 'start_hour' in locals() and 'duration_hours' in locals() and start_hour is not None and duration_hours is not None:
        start_time = loc_df_energy_balance.index[0] + pd.Timedelta(hours=start_hour)
        end_time = start_time + pd.Timedelta(hours=duration_hours)
        plt.xlim([start_time, end_time])
    else:
        plt.xlim([loc_df_energy_balance.index.min(), loc_df_energy_balance.index.max()])

    # show the plot
    plt.show()

    # save the plot as pdf
    fig.savefig(os.path.join(figure_path, str(time_invervall) + '_time_resolved_energy_balance.pdf'), bbox_inches='tight')

    cprint("Done: saved time resolved energy balance plot to disk", 'green')


def make_basic_plots(case_study_name: str, model_name: str, time_invervall: str = 'h', start_hour: int = 0, duration_hours: int = 24*7):
    """Generates basic plots for the given case study and model.

    :param case_study_name: name of the case study
    :type case_study_name: str
    :param model_name: name of the model to be plotted
    :type model_name: str
    :param time_invervall: Sets the time interval for resampling the data (i.e. 'H' for hourly), defaults to 'W' (weekly), defaults to 'h'
    :type time_invervall: str, optional
    :param start_hour: index of the starting our of the range in the plot time axis, defaults to 0
    :type start_hour: int, optional
    :param duration_hours: duration of time steps to plot in the time axis, defaults to 24*7 (one week)
    :type duration_hours: int, optional
    """

    config = load_config()

    figure_path = os.path.join(case_study_name, config['scenario_dir'], model_name, config['plot_dir'])

    model_output_data = read_output_from_disk(case_study_name, model_name, config)
    model_input_data = load_data_from_disk(case_study_name, model_name, config)

    dict_nodes = extract_node_centroids(model_input_data)
    dict_shapes = extract_node_shapes(model_input_data)

    plot_investment_decisions(model_input_data, model_output_data, dict_nodes, dict_shapes, figure_path)
    plot_energy_balance(merge_time_series(model_input_data, model_output_data), figure_path)
    plot_time_resolved(merge_time_series(model_input_data, model_output_data), figure_path, time_invervall=time_invervall, start_hour=start_hour, duration_hours=duration_hours)


# make a plot of the buildings
def plot_HD_interactive(gdf_buildings: gpd.GeoDataFrame):
    """Generates an interactive map of the buildings with their yearly heat demand.

    :param gdf_buildings: geodataframe with the buildings and their yearly heat demand
    :type gdf_buildings: gpd.GeoDataFrame
    :return: an interactive map of the buildings with their yearly heat demand
    :rtype: folium.Map
    """

    gdf_buildings['YearlyDemand_MWh'] = gdf_buildings['YearlyDemand'] / 1000
    m = gdf_buildings.explore(
    column='YearlyDemand_MWh',
    scheme='fisherjenks',
    k = 10,
    cmap='coolwarm',
    legend=True,
    name='Buildings',
    tiles='CartoDB positron',
    zoom_start=14,
    fit_bounds=True,
    style_kwds={'fillOpacity': 0.7, 'color': 'black', 'weight': 0.5},
    width=800, height=600,
    control_scale=True,
    legend_kwds={
        'caption': 'Yearly Heat Demand in MWh/building',
        'caption_font_size': '12px',
        'fmt': '{:.0f}'
    }
    )
    return m
 

if __name__ == '__main__':
    case_study_name = 'Puertollano_open_data'
    model_name = 'hotwater_01'

    config = load_config()
    
    figure_path = os.path.join(case_study_name, config['scenario_dir'], model_name, config['plot_dir'])

    model_output_data = read_output_from_disk(case_study_name, model_name, config)
    model_input_data = load_data_from_disk(case_study_name, model_name, config)

    dict_nodes = extract_node_centroids(model_input_data)
    dict_shapes = extract_node_shapes(model_input_data)

    print(dict_nodes)

    plot_investment_decisions(model_input_data, model_output_data, dict_nodes, dict_shapes, figure_path)
    plot_energy_balance(merge_time_series(model_input_data, model_output_data), figure_path)
    plot_time_resolved(merge_time_series(model_input_data, model_output_data), figure_path, time_invervall='W')
    





    

