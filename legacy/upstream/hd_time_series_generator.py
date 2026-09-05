import numpy as np
import pandas as pd
from termcolor import cprint
import os
from numba import jit
from legacy.upstream import data
import geopandas as gpd
from legacy.upstream.data import load_temp_data

def prepare_temp_out(scenario: str, config: dict) -> pd.DataFrame:
    """Prepare the outside temperature data for the simulation.

    Based on the datetime index, the function adds a column to indicate whether the day is a business day or not.

    :param scenario: _description_
    :type scenario: str
    :param config: configuration dictionary
    :type config: dict
    :return: pandas dataframe with the outside temperature
    :rtype: pd.DataFrame
    """
    df_temperature = load_temp_data(scenario, config)

    # add info if it is a buisness day or not
    df_temperature['businesday'] = df_temperature['time'].apply(lambda x: x.weekday() <= 4)

    # rename the columns
    df_temperature.columns = ['time', 'temperature', 'businesday']

    return df_temperature


def write_hdts_to_disk(df_heating: pd. DataFrame, casestudy: str, config: dict):
    """Write the heating demand time series to disk.

    This function checks if the directory exists and creates it if not. Then it saves the heating demand time series to a CSV file.
    For large datasets, this can take a while. Cosider other data formats if this becomes a problem.

    :param df_heating: pandas dataframe with the heating demand for buildings
    :type df_heating: pd.DataFrame
    :param casestudy: name of the case study
    :type casestudy: str
    :param config: configuration dictionary
    :type config: dict
    """

    # check if directory exists
    data_path = os.path.join(casestudy)
    if not os.path.exists(data_path):
        os.makedirs(data_path)
    df_heating.to_csv(os.path.join(data_path,config['building_data']['building_TS']), index=False, sep=',')


## new functions for the fast implementation
def fast_TS_generator(case_study_name: str, save_to_disk = False) -> pd.DataFrame: 
    """Performs a complete run of the time series generator for a given case study.

    This function loads the necessary data, processes it, and generates time series data for buildings.
    Steps:
    
    1. Load the configuration file.
    2. Load the transition matrices for weekdays (WD) and weekends (WE).
    3. Load and process temperature data.
    4. Initialize arrays for internal and solar gains.
    5. Load building data as a GeoDataFrame.
    6. Convert building data to dwelling-specific arrays.
    7. Generate time series for each dwelling using thermal properties and external conditions.
    8. Aggregate dwelling-level time series into building-level time series.
    9. Save the resulting time series to disk as a CSV file.

    :param case_study_name: name of the case study
    :type case_study_name: str
    :param save_to_disk: set to true if you want to write the results into a csv. file, defaults to False
    :type save_to_disk: bool, optional
    :return: a pandas dataframe with the generated time series data in hourly resolution for all buildings
    :rtype: pd.DataFrame
    """

    print("Start processing for case study: ", case_study_name)
    # load config file
    config = data.load_config()
    cprint("Done: load config", 'green')


    # load the transition matrix
    path = os.path.join(case_study_name,config["parameter_dir"],config["MCMC_dir"])
    df_transition_matrix_WD = pd.read_excel(os.path.join(path, config["case_study_data"]["transition_matrix_WD"]), index_col=0)
    df_transition_matrix_WE = pd.read_excel(os.path.join(path, config["case_study_data"]["transition_matrix_WE"]), index_col=0)
    
    ar_transition_matrix_WD = df_transition_matrix_WD.to_numpy()
    ar_transition_matrix_WE = df_transition_matrix_WE.to_numpy()
    cprint("Done: load transition matrix", 'green')

    # load the temperature data
    df_temperature = prepare_temp_out(case_study_name, config)
    ar_Tout = df_temperature["temperature"].to_numpy()
    ar_WDWE = calc_WD_WE_sequence(df_temperature)
    cprint("Done: load temperature data", 'green')

    # load the time series data from solar gain and internal gain
    df_solar_gain = data.load_solar_gain_data(case_study_name, config)
    ar_solar_gain_profile = df_solar_gain['solar_gain'].to_numpy()

    # load geodataframe
    gdf_buildings = gpd.read_file(os.path.join(case_study_name, config["building_data"]["buildings_gdf"]))
    cprint("Done: load building data", 'green')

    # convert the dataframe to individual arrays; from building data to dwelling data
    ar_building_id, ar_num_of_dwellings, ar_GeneralisedThermCond, ar_GeneralisedThermCap, ar_MaxDemand, ar_temp_setpoint, ar_temp_setback, ar_solar_gain_scalor, ar_internal_gain_scalor = convert_buildings_to_dwellings(gdf_buildings)

    cprint(f"Start generating time series for: {len(ar_building_id)} dwellings. Estimated computational time is: {round(len(ar_building_id)/378,0)} seconds.")
    dwelling_HD_TS = fast_generate_building_ts(ar_building_id, ar_GeneralisedThermCond, ar_GeneralisedThermCap, ar_MaxDemand, ar_temp_setpoint, ar_temp_setback, ar_Tout, ar_WDWE, ar_transition_matrix_WD, ar_transition_matrix_WE, ar_solar_gain_profile, ar_solar_gain_scalor, ar_internal_gain_scalor)
    cprint("Done: generate time series", 'green')

    # convert the dwelling time series to building time series
    df_building_HD_TS  = convert_dwelling_TS_to_building_TS(dwelling_HD_TS, ar_building_id)

    # check the results
    check_yearly_demand_deviation(gdf_buildings, df_building_HD_TS)

    # generate a sum over all buildings
    df_building_HD_TS['sum'] = df_building_HD_TS.iloc[:, 1:].sum(axis=1)

    # write the time series to disk
    if save_to_disk:
        print("Write time series to disk")
        # save as csv file
        df_building_HD_TS.to_csv(os.path.join(case_study_name, config['building_data']['building_TS']), index=False)
        cprint("Done: write time series to disk", 'green')

    return df_building_HD_TS

def calc_WD_WE_sequence(df_temperature: pd.DataFrame) -> np.ndarray:
    """Calculates a seqeunce of business days and weekends based on the date of the dataset.

    :param df_temperature: pandas dataframe with the temperature data and the date
    :type df_temperature: pd.DataFrame
    :return: numpy array with the sequence of business days and weekends
    :rtype: np.ndarray
    """
    # buisnesday = 1, weekend = 0
    df_temp_local = df_temperature.copy()

    # Ensure the index is a datetime type
    df_temp_local['time'] = pd.to_datetime(df_temp_local['time'])
    df_temp_local.set_index('time', inplace=True)

    # Regroup the df_temp to daily values
    df_temp_local = df_temp_local.groupby(df_temp_local.index.date).mean()

    # export the column buisnesday as a np array
    ar_WEWDseq = df_temp_local["businesday"].to_numpy()
   
    return ar_WEWDseq

def check_yearly_demand_deviation(gdf_buildings: gpd.GeoDataFrame, df_heat_demand: pd.DataFrame):
    """Check deviation of the yearly demand of the buildings from the sum of the heating demand time series.

    :param gdf_buildings: geodataframe with the building data
    :type gdf_buildings: gpd.GeoDataFrame
    :param df_heat_demand: dataframe with the heating demand time series
    :type df_heat_demand: pd.DataFrame
    """
    # calculate the difference on building level
    # YearlyDemand is kWh/a and the hourly table is kW at a 1 h step, so the
    # column sum is already kWh for the modelled year.
    diff_abs = gdf_buildings['YearlyDemand'] - df_heat_demand.iloc[:, 1:].sum(axis=0).values
    diff_percent = diff_abs / gdf_buildings['YearlyDemand'] * 100
    diff_percent[gdf_buildings['YearlyDemand'] == 0] = 0

    # calc max deviation of absolute values
    max_diff_percent = abs(diff_percent).max()

    # calc the total deviation of the absolute values
    total_diff = (diff_abs).sum()
    total_diff_percent = total_diff / gdf_buildings['YearlyDemand'].sum() * 100

    if max_diff_percent > 30:
        cprint("Warning: The yearly demand of a building deviates from the sum of the heating demand time series!", 'yellow')
        cprint(f"Max deviation on single building level: {max_diff_percent:.2f} %", 'yellow')
    else:
        cprint("The yearly demand of of all building is close to the sum of the heating demand time series", 'green')
        cprint(f"Max deviation on single building level: {max_diff_percent:.2f} %", 'green')

    if abs(total_diff_percent) > 3:
        cprint("Warning: The total yearly demand of the buildings deviates from the sum of the heating demand time series!", 'yellow')
        cprint(f"Total deviation in heat demand: {total_diff_percent:.2f} %", 'yellow')
    else:
        cprint("The yearly demand of all buildings is close to the sum of the heating demand time series", 'green')
        cprint(f"Total deviation in heat demand: {total_diff_percent:.2f} %", 'green')

    

def convert_buildings_to_dwellings(gdf_buildings: gpd.GeoDataFrame) -> tuple:
    """Convert the building data to dwelling data and split the data into individual arrays.

    This function takes the building data and splits it into individual arrays for each dwelling. The arrays are then used to generate the heating demand time series.

    :param gdf_buildings: geodataframe with the building data
    :type gdf_buildings: gpd.GeoDataFrame
    :return: individual arrays for the different building properties
    :rtype: tuple
    """
    df_buildings = gdf_buildings[['building_id','number_of_dwellings','GeneralisedThermCond','GeneralisedThermCap','MaxDemand','temp_setpoint','temp_setback','InternalGainFactor','SolarGainFactor']].copy()

    # convert the buidling_id to int after spliting the string
    df_buildings['building_id_int'] = (df_buildings['building_id'].str.split('_').str[1].astype(int))

    # count the number of buildings with zero dwellings
    buildings_zero_dwellings = len(df_buildings[df_buildings['number_of_dwellings'] == 0])
    # count the number of buildings with zero dwellings
    total_number_of_dwelling = df_buildings['number_of_dwellings'].sum()
    total_count = int(buildings_zero_dwellings + total_number_of_dwelling)


    # write the required building properties into a np.array -> the index in the array takes care of the position and has to be merged back afterwards
    ar_building_id = np.empty(total_count)
    ar_num_of_dwellings = np.empty(total_count)
    ar_GeneralisedThermCond = np.empty(total_count)
    ar_GeneralisedThermCap = np.empty(total_count)
    ar_MaxDemand = np.empty(total_count)
    ar_temp_setpoint = np.empty(total_count)
    ar_temp_setback = np.empty(total_count)
    ar_solar_gain_scalor = np.empty(total_count)
    ar_internal_gain_scalor = np.empty(total_count)

    df_buildings.head()

    # assign the values from the dataframe to the array
    array_index = 0

    for iter in range(len(df_buildings)):
        temp_num_of_dwellings = int(df_buildings.at[iter,'number_of_dwellings'])


        if temp_num_of_dwellings < 2:
            ar_building_id[array_index] = (df_buildings.at[iter,'building_id_int'])
            ar_num_of_dwellings[array_index] = temp_num_of_dwellings
            ar_GeneralisedThermCond[array_index] = df_buildings.at[iter,'GeneralisedThermCond']
            ar_GeneralisedThermCap[array_index] = df_buildings.at[iter,'GeneralisedThermCap']
            ar_MaxDemand[array_index] = df_buildings.at[iter,'MaxDemand']
            ar_temp_setpoint[array_index] = df_buildings.at[iter,'temp_setpoint']
            ar_temp_setback[array_index] = df_buildings.at[iter,'temp_setback']
            ar_solar_gain_scalor[array_index] = df_buildings.at[iter,'SolarGainFactor']
            ar_internal_gain_scalor[array_index] = df_buildings.at[iter,'InternalGainFactor']

            array_index += 1
        else:
            for sub_iter in range(temp_num_of_dwellings):
                ar_building_id[array_index] = (df_buildings.at[iter,'building_id_int'])
                ar_num_of_dwellings[array_index] = temp_num_of_dwellings
                ar_GeneralisedThermCond[array_index] = df_buildings.at[iter,'GeneralisedThermCond'] / temp_num_of_dwellings
                ar_GeneralisedThermCap[array_index] = df_buildings.at[iter,'GeneralisedThermCap'] / temp_num_of_dwellings
                ar_MaxDemand[array_index] = df_buildings.at[iter,'MaxDemand'] / temp_num_of_dwellings
                ar_temp_setpoint[array_index] = df_buildings.at[iter,'temp_setpoint']
                ar_temp_setback[array_index] = df_buildings.at[iter,'temp_setback']
                ar_solar_gain_scalor[array_index] = df_buildings.at[iter,'SolarGainFactor'] / temp_num_of_dwellings
                ar_internal_gain_scalor[array_index] = df_buildings.at[iter,'InternalGainFactor'] / temp_num_of_dwellings

                array_index += 1

    return ar_building_id, ar_num_of_dwellings, ar_GeneralisedThermCond, ar_GeneralisedThermCap, ar_MaxDemand, ar_temp_setpoint, ar_temp_setback, ar_solar_gain_scalor, ar_internal_gain_scalor


def convert_dwelling_TS_to_building_TS(dwelling_HD_TS: np.ndarray, ar_building_id: np.ndarray) -> pd.DataFrame:
    """Sum dwelling heat-demand power into a floating-point building kW table.

    This function takes the heating demand time series for all dwellings and sums them up for each building. The resulting data is then converted to a pandas dataframe.

    :param dwelling_HD_TS: numpy array with the heating demand time series for all dwellings
    :type dwelling_HD_TS: np.ndarray
    :param ar_building_id: numpy array with the building id for each dwelling
    :type ar_building_id: np.ndarray
    :return: pandas dataframe with the heating demand time series for all buildings
    :rtype: pd.DataFrame
    """
    # transfrom the array down to a dataframe
    df_dwelling_HD_TS = pd.DataFrame(dwelling_HD_TS.T)

    # add the building id to the dataframe
    df_dwelling_HD_TS.insert(0, 'building_id', ar_building_id)

    # regroup the data by building id and sum the values
    df_dwelling_HD_TS = df_dwelling_HD_TS.groupby('building_id').sum()

    # transpose the datatframe
    df_dwelling_HD_TS = df_dwelling_HD_TS.T

    # rename the columns 
    df_dwelling_HD_TS.columns = ['building_' + str(i) for i in range(0, len(df_dwelling_HD_TS.columns) )]

    # add a column for the hour
    df_dwelling_HD_TS.insert(0, 'hour', range(1, len(df_dwelling_HD_TS) + 1))

    # The generator already calculates heat-demand power in kW. Keep it as
    # float64; converting to Wh and uint32 here caused a hidden x1000 contract
    # and discarded fractional demand values.
    df_dwelling_HD_TS.iloc[:, 1:] = df_dwelling_HD_TS.iloc[:, 1:].astype('float64')

    return df_dwelling_HD_TS

#@jit -> vectorized implementation instead
def fast_generate_building_ts(ar_building_id: np.ndarray, ar_GeneralisedThermCond: np.ndarray, ar_GeneralisedThermCap: np.ndarray, ar_MaxDemand: np.ndarray, ar_temp_setpoint: np.ndarray, ar_temp_setback: np.ndarray, ar_Tout: np.ndarray, ar_WDWE: np.ndarray, ar_transition_matrix_WD: np.ndarray, ar_transition_matrix_WE: np.ndarray, ar_solar_gain_profile: np.ndarray, scalor_solar_gain: np.ndarray, scalor_internal_gain: np.ndarray) -> np.ndarray:
    """This function generates the heating demand time series for all dwellings.

    This function provides a fast implementation of the heating demand time series generator. It uses vectorized operations to speed up the calculations.
    All input data has to be provided as numpy arrays. The function returns a numpy array with the heating demand time series for all dwellings.

    :param ar_building_id: A numpy array with the corresponding building id for each dwelling.
    :type ar_building_id: np.ndarray
    :param ar_GeneralisedThermCond: A numpy array with the generalised thermal conductance for each dwelling.
    :type ar_GeneralisedThermCond: np.ndarray
    :param ar_GeneralisedThermCap: A numpy array with the generalised thermal capacity for each dwelling.
    :type ar_GeneralisedThermCap: np.ndarray
    :param ar_MaxDemand: A numpy array with the maximum heating demand for each dwelling.
    :type ar_MaxDemand: np.ndarray
    :param ar_temp_setpoint: A numpy array with the setpoint temperature for each dwelling.
    :type ar_temp_setpoint: np.ndarray
    :param ar_temp_setback: A numpy array with the setback temperature for each dwelling.
    :type ar_temp_setback: np.ndarray
    :param ar_Tout: A numpy array with the outside temperature for each time step.
    :type ar_Tout: np.ndarray
    :param ar_WDWE: A numpy array with the sequence of business days and weekends.
    :type ar_WDWE: np.ndarray
    :param ar_transition_matrix_WD: A numpy array with the transition matrix for weekdays.
    :type ar_transition_matrix_WD: np.ndarray
    :param ar_transition_matrix_WE: A numpy array with the transition matrix for weekends.
    :type ar_transition_matrix_WE: np.ndarray
    :param ar_solar_gain_profile: A numpy array with the general solar gain (= solar irradiation) for each time step.
    :type ar_solar_gain_profile: np.ndarray
    :param scalor_solar_gain: A numpy array with the scaling factor for the solar gain for each dwelling.
    :type scalor_solar_gain: np.ndarray
    :param scalor_internal_gain: A numpy array with the scaling factor for the internal gain for each dwelling.
    :type scalor_internal_gain: np.ndarray
    :return: A numpy array with the heating demand time series for all dwellings.
    :rtype: np.ndarray
    """

    # initialise an empty array for the heating demand for all dwellings and the time steps
    ar_heating_demand = np.zeros((len(ar_Tout), len(ar_building_id)))

    # Vectorized implementation
    # Generate occupancy time series for all buildings
    yearly_states = np.array([calc_yearly_occupancy(ar_WDWE, ar_transition_matrix_WD, ar_transition_matrix_WE) for _ in range(len(ar_building_id))])
    ar_Tset = np.array([assign_temp(yearly_states[i], ar_temp_setpoint[i], ar_temp_setback[i]) for i in range(len(ar_building_id))]).T
  
    # calculte the solar gain with same profile for all buildings
    ar_solar_gain = ar_solar_gain_profile
    # calculate the internal gaing profile with a different profile for each building
    ar_internal_gain = yearly_states.T.astype(int)

    # Generate heating demand time series for all buildings
    ar_heating_demand = np.array([
        fast_calculate_hd_ts(ar_Tout, ar_Tset[:, j], ar_internal_gain[:,j], ar_solar_gain, ar_MaxDemand[j], ar_GeneralisedThermCond[j], ar_GeneralisedThermCap[j], scalor_solar_gain[j], scalor_internal_gain[j])[0]
        for j in range(len(ar_building_id))
    ]).T

    return ar_heating_demand


@jit
def fast_calculate_hd_ts(ar_Tout: np.ndarray, ar_Tset: np.ndarray, ar_internal_gain: np.ndarray, ar_solar_gain: np.ndarray, max_heat_power=15.0, thermal_coductance=0.12, thermal_storage_capacity=7.0, scalor_solar_gain=0.0, scalor_internal_gain=0.0) -> tuple:
    """Fast calculation of the heating demand time series for a single dwelling.

    This function gets precompiled to speed up the calculations. All parameters and time series in the input are for a single dwelling.
    
    :param ar_Tout: A numpy array with the outside temperature for each time step in °C.
    :type ar_Tout: np.ndarray
    :param ar_Tset: A numpy array with the setpoint temperature for each time step in °C.
    :type ar_Tset: np.ndarray
    :param ar_internal_gain: A numpy array with the internal gain for each time step in kW.
    :type ar_internal_gain: np.ndarray
    :param ar_solar_gain: A numpy array with the solar gain for each time step in kW.
    :type ar_solar_gain: np.ndarray
    :param max_heat_power: Maximum heating power for that dwelling in kW, defaults to 15.0
    :type max_heat_power: float, optional
    :param thermal_coductance: generalised thermal conductance in kW/K for the specific dwelling, defaults to 0.12
    :type thermal_coductance: float, optional
    :param thermal_storage_capacity: generalised thermal storage capacity in kWh/K for the specific dwelling, defaults to 7.0
    :type thermal_storage_capacity: float, optional
    :param scalor_solar_gain: Scaling factor for the solar gain of the specific dwelling, defaults to 0.0
    :type scalor_solar_gain: float, optional
    :param scalor_internal_gain: Scaling factor for the internal gain for the specific dwelling, defaults to 0.0
    :type scalor_internal_gain: float, optional
    :return: returns the heating demand time series and the indoor temperature time series for the dwelling
    :rtype: tuple
    """
    
    # initialise the heating demand array
    ar_T_in = np.zeros((len(ar_Tout)))
    ar_actual_heat_power = np.zeros((len(ar_Tout))) # the power that is accually used for heating

    # set values for the first time step
    ar_T_in[0] = ar_Tset[0] + (np.random.randn() * 0.1)

    # calculate a heating demand for time step 0 just based on the current termal losses
    Q_losses = thermal_coductance*(ar_T_in[0] - ar_Tout[0])
    #heating_for_T_change = thermal_storage_capacity * (ar_Tset[0] - ar_T_in[0]) # required heating to change the temperature
    heat_demand = Q_losses #+ heating_for_T_change
    actual_heating_power = max(0, min(max_heat_power, heat_demand))
    ar_actual_heat_power[0] = actual_heating_power

    # iterate over the time steps
    for i in range(1, len(ar_Tout)):
        # calculate the losses
        Q_losses = thermal_coductance*(ar_T_in[i-1] - ar_Tout[i-1]) # for t-1
        # calculate the internal gain
        total_gain = ar_internal_gain[i-1] * scalor_internal_gain + ar_solar_gain[i-1] * scalor_solar_gain # for t-1
        # calculate the current indoor temperature
        T_in = ar_T_in[i-1] + (ar_actual_heat_power[i-1] + total_gain - Q_losses) / thermal_storage_capacity
        # calculate the heating demand
        heating_for_T_change = thermal_storage_capacity * (ar_Tset[i] - T_in)
        #calculate the heat demand
        heat_demand = thermal_coductance * (T_in- ar_Tout[i]) + heating_for_T_change
        # calculate the actual heating power

        actual_heating_power = max(0,min(max_heat_power, heat_demand))

        # set the calculated values as new values for the next iteration
        ar_T_in[i] = T_in
        ar_actual_heat_power[i] = actual_heating_power


    return ar_actual_heat_power, ar_T_in



@jit
def calc_yearly_occupancy(ar_WDWE: np.ndarray, ar_transition_matrix_WD: np.ndarray, ar_transition_matrix_WE: np.ndarray) -> np.ndarray:
    """Calculates the yearly occupancy time series for a given sequence of business days and weekends by Markov Chain Monte Carlo (MCMC) simulation.

    :param ar_WDWE: A numpy array with the sequence of business days and weekends.
    :type ar_WDWE: np.ndarray
    :param ar_transition_matrix_WD: A numpy array with the transition matrix for weekdays.
    :type ar_transition_matrix_WD: np.ndarray
    :param ar_transition_matrix_WE: A numpy array with the transition matrix for weekends.
    :type ar_transition_matrix_WE: np.ndarray
    :return: A numpy array with the yearly occupancy time series in hourly resolution.
    :rtype: np.ndarray
    """
    states_WD = fast_generate_mcchain(ar_transition_matrix_WD)
    states_WE = fast_generate_mcchain(ar_transition_matrix_WE)

    yearly_states = np.empty((len(ar_WDWE) * 24)) 

    #if yearly_states.max() != yearly_states.max():
    #    print("Warning 1: The yearly occupancy time series contains NaN values. Please check the input data.")

    for i in range(0, len(ar_WDWE)):
        if ar_WDWE[i] == 1:
            yearly_states[i*24:i*24+24] = states_WD
        else:
            yearly_states[i*24:i*24+24] = states_WE

    return yearly_states


def assign_temp(ar_states: np.ndarray, T_setpoint = 22.0, T_setback = 18.0) -> np.ndarray:
    """Assigns the setpoint temperature to the states based on the occupancy time series.

    This function takes the occupancy time series and assigns the setpoint temperature to the states. The setpoint temperature is assigned to the states where the occupancy is 1 (occupied) and the setback temperature is assigned to the states where the occupancy is 0 (unoccupied).
    The function returns the states with the assigned temperatures.

    :param ar_states: A numpy array with the occupancy time series for one year in hourly resolution.
    :type ar_states: np.ndarray
    :param T_setpoint: Setpoint temperature in °C, defaults to 22.0
    :type T_setpoint: float, optional
    :param T_setback: Setback temperature in °C, defaults to 18.0
    :type T_setback: float, optional
    :return: A numpy array with the setpoint temperature.
    :rtype: np.ndarray
    """
    # assign the setpoint temperature to the states
    ar_states = np.where(ar_states > 0.5, T_setpoint, T_setback)
    return ar_states

@jit
def fast_generate_mcchain(ar_transition_matrix: np.ndarray, ini_prob = 0.2, resample = True) -> np.ndarray:
    """Generates a Markov Chain Monte Carlo (MCMC) chain based on the transition matrix for one day.

    :param ar_transition_matrix: A numpy array with the transition matrix for one day.
    :type ar_transition_matrix: np.ndarray
    :param ini_prob: probability for active for the first time step, defaults to 0.2
    :type ini_prob: float, optional
    :param resample: boole flag: if True the 10-min profile is converted to hourly resolution, defaults to True
    :type resample: bool, optional
    :return: a numpy array with the MCMC chain in hourly (or 10-min) resolution
    :rtype: np.ndarray
    """
    
    # initialise an empty array to store the MCMC chain
    states = np.empty((144,1)) #, dtype=bool)


    # set fist state to a random state
    r_ini = np.random.rand()
    if r_ini < ini_prob:
        states[0] = 1
    else:
        states[0] = 0

    # iterate over the MCMC chain
    for i in range(1, len(ar_transition_matrix)):
        # get the previous state
        prev_state = states[i - 1]

        r = np.random.rand()

        # get the transition probabilities for the previous state
        if prev_state: # if previsou state is True, use columns 1
            if r < ar_transition_matrix[i - 1, 1]:
                states[i] = 1
            else:   
                states[i] = 0
        else: # if previous state is False, use columns 0 
            if r < ar_transition_matrix[i - 1, 0]:
                states[i] = 1          
            else:
                states[i] = 0

    # resample the states to 24 insted of 144 steps
    states = np.reshape(states, (-1, 6))
    # calculate the average of the states for each day
    states = np.sum(states, axis=1)/6
    # convert the states to a binary array
    states = np.where(states > 0.5, 1, 0)
  

    return states




if __name__ == "__main__":
    case_study = "Frauental"
    fast_TS_generator(case_study)
    pass
