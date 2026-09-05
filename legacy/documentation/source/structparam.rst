.. _struct_param:


Project Structure and Settings
==============================

In this section, you will find an overview of the project's general structure from a user perspective, the required formats for all input data, and the available settings and parameters that can be adjusted to customize your analysis.

.. seealso::

   For more details on the conceptual model design, the complete mathematical formulation, 
   time horizons for input data, or aggregation, please refer to the Supplementary Material: 
   `https://doi.org/10.3217/7ep0n-27377 <https://doi.org/10.3217/7ep0n-27377>`_.



Organizational Structure: Case Studies and Scenarios
----------------------------------------------------

The project is structured into two hierarchical levels:

- **Case Study**:  
  Refers to all analyses of one particular spatial region.  
  The first two steps (geodata acquisition and creation of heating demand time series for all buildings) are always related to a case study.  
  Each case study can contain several scenarios.

- **Scenario**:  
  Refers to a specific analysis within a case study, where different parameters, inputs, and costs are investigated for the same spatial region.  
  For each scenario, clustering and optimization can be executed, enabling comparison of, for example, different waste heat sources, temporal profiles, or various cost assumptions.

Folder Structure
-----------------

When the script is run with a ``case_study_name`` or a ``scenario_name`` that does not yet exist, a new folder structure is generated.  
It automatically contains all required files with default values.  
The default files are defined in the ``default`` folder.

If a script is run again, existing **input** and **parameter** sheets are **not overwritten**.  
Only the **output files** (e.g., results) corresponding to the executed step are updated.

The general folder structure is organized as follows:
 
 
::

   project_root/
   ├── default/                        # Default input templates used for scenario configuration
   │   ├── input_ParameterCosts.xlsx          # Default cost parameters (e.g. piping, investment)
   │   ├── input_HeatGenerationUnits.xlsx     # Default waste heat unit definitions
   │   └── ...
   ├── Conda-Activation-Scripts/       # Activation script(s) for setting up the Python environment
   ├── docs/                           # Sphinx documentation source files
   ├── logs/                           # Log files from optimization model runs
   ├── [case_study_name]/              # Top-level folder for one spatial region (case study)
   │   ├── general_parameters/         # Static settings specific to this case study
   │   │   ├── building_energy_data/           # Heat demand by building type and structure
   │   │   └── MCMC_data/                      # Transition matrices, weather, and solar data
   │   ├── Building_Data.geojson              # Spatial data for the region (from OSM or other sources)
   │   ├── Building_TS.csv                    # Generated heat demand time series
   │   └── scenarios/                         # Directory for all scenarios
   │       ├── [scenario_name]/               # One or more scenarios for analysis within a case study
   │       │   ├── data/                      # Scenario-ready data for optimization
   │       │   ├── input/                     # User-defined, scenario-specific parameters
   │       │   │   ├── input_HeatGenerationUnits.xlsx  # Waste heat source definitions
   │       │   │   ├── input_ParameterCosts.xlsx       # Cost and design parameters
   │       │   │   └── input_WastHeatProfiles.xlsx     # Temporal profiles for waste heat
   │       │   ├── output/                    # Raw output files from the optimization model
   │       │   ├── plots/                     # Standard plots for visualisation of results
   │       │   └── expost/                    # Post-processing and analysis results
   │       └── ...
   ├── _config.yaml                     # Central configuration for file paths and defaults
   ├── environment.yaml                 # Conda environment requirements
   ├── main.ipynb                       # Main notebook to run the full data workflow
   ├── prepare_geodata.py              # Module: Prepare and download geospatial data
   ├── hd_time_series_generator.py     # Module: Generate stochastic heat demand time series
   ├── clustering.py                   # Module: Cluster demand and propose network layouts
   ├── model.py                        # Module: Optimization model for energy system configuration
   ├── data.py                         # Module: Utilities for data handling and preprocessing
   ├── utils.py                        # Module: General helper functions
   └── visualisation.py                # Module: Visual output and plotting


 
Settings and Input Parameters
-----------------------------


This section provides an overview of the project structure, file naming conventions, required input formats, and the adjustable settings and parameters.

File and Folder Naming
~~~~~~~~~~~~~~~~~~~~~~

The file ``_config.yaml`` specifies the naming of all files and folders used throughout the workflow.  
Be aware: **Changing names retroactively may lead to errors when rerunning previously configured scenarios.**

Default Files
~~~~~~~~~~~~~

The folder ``default/`` contains all default input files and parameter sheets used when a new case study or scenario is created.  
To change default values, simply edit the corresponding files — but **do not change the file structure**, as this could break functionality.

Building Typology
~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/general_parameters/building_energy_data/BuildingTypology.xlsx``

This file defines the annual heating demand in kWh/m²/year for various building types.  
It distinguishes between building age (year of construction) and building use (e.g., residential, commercial).  
You should adjust these values for your specific region and climate zone.

- Source: TABULA Web Tool [Tabula]_.
- Custom data can be included for specific communities.
- Construction year categories can be added or adapted.

Outside Temperature
~~~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/general_parameters/MCMC_data/outside_temp.xlsx``

This file contains:

- ``time`` column: hourly datetime format (usually 8760 entries per year)
- ``T_out`` column: hourly outdoor temperature in °C for the case study location

Used to generate time-dependent occupancy and heating profiles.

- Tip: Data can be sourced from Renewables Ninja [Renewables.ninja]_.

Solar Gain
~~~~~~~~~~

File: ``[case_study_name]/general_parameters/MCMC_data/solar_gain.xlsx``

- ``time`` column: must align with ``outside_temp.xlsx``
- ``solar_gain`` column: global solar irradiation in W/m²

Used to compute passive solar heat gains per building, scaled with individual factors.

Transition Matrices
~~~~~~~~~~~~~~~~~~~

Files:

- ``transition_matrix_WD.xlsx`` — for weekdays
- ``transition_matrix_WE.xlsx`` — for weekends

Path: ``[case_study_name]/general_parameters/MCMC_data/``

Each matrix provides 144 values per hour (10-minute resolution), representing probabilities for four transition states:

- 0→0, 0→1, 1→0, 1→1

These matrices are used to generate stochastic active occupancy profiles.

- Based on Time Use Survey data.
- See [MCMC-Process]_.

Heat Generation Units
~~~~~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/[scenario_name]/input/input_HeatGenerationUnits.xlsx``

Each row represents a waste heat unit with the following columns:

- ``unit``: Name (must match a column name in ``input_WastHeatProfiles.xlsx``)
- ``lat``, ``lon``: Coordinates of the unit
- ``isWH``, ``isBoiler``, ``isTES``: Binary (1/0) to define unit type

   - ``isWH`` = Waste Heat Unit (profile limited)
   - ``isBoiler`` = Dispatchable boiler (e.g., gas, P2H, HP)
   - ``isTES`` = Thermal energy storage
   
- ``O&M and Fuel Costs``: €/kWh (already efficiency-adjusted)
- ``Power Investment Costs``: €/kW (annualized)
- ``Storage Investment Costs``: €/m³ (only for TES)
- ``Storage loss rate``: %/h (only for TES)

Waste Heat Profiles
~~~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/[scenario_name]/input/input_WastHeatProfiles.xlsx``

- ``hour`` column: 0–8759
- One column per unit defined in ``input_HeatGenerationUnits.xlsx``
- Values: Maximum thermal output (mass flow) per hour

Parameter and Cost Definitions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/[scenario_name]/input/input_ParameterCosts.xlsx``

All adjustable technical and financial parameters are defined in the following table.  
The names correspond to the naming in the code.  
The default values provide rough estimations and should be adapted for the specific case study.  



.. list-table:: 
   :widths: 30 15 10 10 35
   :header-rows: 1

   * - Name
     - Parameter
     - Default value
     - Unit
     - Description
   * - Costs for non-supplied heat
     - pCostHNS
     - 100
     - €/kWh
     - Penalty for unmet heat demand
   * - Pumping costs
     - pCostPumping
     - 0.00001
     - €/(m³/m)
     - Energy cost for water circulation
   * - Supply temperature
     - pTsupply
     - 90
     - °C (or K)
     - Constant level for supply
   * - Return temperature
     - pTreturn
     - 55
     - °C (or K)
     - Constant level for return
   * - Pipe base investment cost
     - pPipeCostIni
     - 14.16
     - €/m
     - Base cost for installing pipes
   * - Initial pipe flow capacity
     - pMassFlowIni
     - 0.8
     - m³/h
     - Smallest pipe capacity
   * - Pipe cost slope
     - pPipeCostsSlope
     - 0.133
     - €/(m m³/h)
     - Cost per flow unit
   * - Number of heat clusters
     - param_cluster_size
     - 80
     - none
     - Number of demand clusters
   * - Connection cost per building
     - cost_DH_connect_building
     - 83.33
     - €/building
     - Fixed connection cost
   * - Area-based network investment
     - cost_DH_connect_area
     - 1.416
     - €/m²
     - For in-cluster piping
   * - Power-based connection cost
     - cost_DH_connect_power
     - 9.17
     - €/kW
     - Installed capacity cost
   * - Hot water demand
     - daily_hot_water_demand
     - 5
     - kWh/dwelling/day
     - Constant domestic hot water usage
   * - Restrict dual heating
     - allow_double_heating
     - 0
     - Binary (0/1)
     - 0 = buildings connected to district heating may not use decentral heating
   * - Time step durration
     - phWeight
     - 1
     - h
     - weight of the timestep

 
 
 
 
  
References
 .. [Tabula] https://webtool.building-typology.eu/#bm
 .. [Renewables.ninja] https://www.renewables.ninja/
 .. [MCMC-Process] `https://doi.org/10.1016/j.segy.2025.100181 <https://doi.org/10.1016/j.segy.2025.100181>`_



Using the Solver
----------------

The optimization model is built using the Pyomo package. A solver must be specified to run the model. This is controlled via the `solver` parameter in the `_config.yaml` file and handled within the `model_run` function in the `model.py` module, where solver-specific options can also be configured.

Two solvers are preconfigured by default:

- **HiGHS**  
  An open-source solver included via the `highspy` package in the environment. No additional setup is required.

- **Gurobi**  
  A commercial solver that can be used free of charge for academic purposes with an academic license. It must be installed separately and properly licensed.

> While HiGHS works reliably for most scenarios, Gurobi is typically much faster, especially for larger or more complex optimization problems.


