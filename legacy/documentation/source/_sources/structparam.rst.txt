.. _struct_param:


Project Structure and Settings
==============================

In this section, you will find an overview of the project's general structure, the required formats for all input data, and the available settings and parameters you can adjust to customize your analysis.



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
 
Project Structure
-----------------
 
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

- Tip: Data can be sourced from Renewables Ninja [Renewables.ninja]_..

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
- ``Storage loss rate %/h``: Only for TES

Waste Heat Profiles
~~~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/[scenario_name]/input/input_WastHeatProfiles.xlsx``

- ``hour`` column: 0–8759
- One column per unit defined in ``input_HeatGenerationUnits.xlsx``
- Values: Maximum thermal output (mass flow) per hour

Parameter and Cost Definitions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

File: ``[case_study_name]/[scenario_name]/input/input_ParameterCosts.xlsx``

All adjustable technical and financial parameters are defined here:

.. list-table:: Parameters
   :widths: 30 20 15 35
   :header-rows: 1

   * - Name
     - Parameter
     - Unit
     - Description
   * - Costs for non-supplied heat
     - pCostHNS = 100
     - €/kWh
     - Penalty for unmet demand
   * - Pumping costs
     - pCostPumping = 0.00001
     - €/m³
     - Energy cost for water circulation
   * - Supply temperature
     - pTsupply = 90
     - °C
     - Constant level for supply
   * - Return temperature
     - pTreturn = 55
     - °C
     - Constant level for return
   * - Pipe base investment cost
     - pPipeCostIni = 14.16
     - €/m
     - Base cost for installing pipes
   * - Initial pipe flow capacity
     - pMassFlowIni = 0.8
     - m³/h
     - Smallest pipe capacity
   * - Pipe cost slope
     - pPipeCostsSlope = 0.133
     - €/m·m³/h
     - Cost per flow unit
   * - Number of heat clusters
     - param_cluster_size = 80
     - -
     - Number of demand clusters
   * - Connection cost per building
     - cost_DH_connect_building = 83.33
     - €/building
     - Fixed connection cost
   * - Area-based network investment
     - cost_DH_connect_area = 1.416
     - €/m²
     - For in-cluster piping
   * - Power-based connection cost
     - cost_DH_connect_power = 9.17
     - €/kW
     - Installed capacity cost
   * - Hot water demand
     - daily_hot_water_demand = 5
     - kWh/dwelling/day
     - Constant domestic hot water usage
   * - Restrict dual heating
     - allow_double_heating = 0
     - Binary (0/1)
     - 0 = buildings connected to district heating may not use decentral heating

 
 


 
References
 .. [Tabula] https://webtool.building-typology.eu/#bm
 .. [Renewables.ninja] https://www.renewables.ninja/
 .. [MCMC-Process] `doi:10.3217/0b20r-jrx54 <doi:10.3217/0b20r-jrx54>`_



Using the Solver
----------------

The optimization model is built using the Pyomo package. A solver must be specified to run the model. This is controlled via the `solver` parameter in the `_config.yaml` file and handled within the `model_run` function in the `model.py` module, where solver-specific options can also be configured.

Two solvers are preconfigured by default:

- **HiGHS**  
  An open-source solver included via the `highspy` package in the environment. No additional setup is required.

- **Gurobi**  
  A commercial solver that can be used free of charge for academic purposes with an academic license. It must be installed separately and properly licensed.

> While HiGHS works reliably for most scenarios, Gurobi is typically much faster, especially for larger or more complex optimization problems.


 
Optimization Model Formulation
------------------------------
The optimisation model (model module) used to finde the best configuration is described here. 

Sets
~~~~

.. list-table:: Model sets
   :widths: 20 40 40
   :header-rows: 1

   * - **Name**
     - **Meaning**
     - **Symbol**
   * - n
     - Heat nodes
     - :math:`n`
   * - h
     - (hourly) Time index
     - :math:`h`
   * - pc
     - Pipe candidate connections
     - :math:`pc`
   * - hg
     - Heat generation units
     - :math:`hg`
   * - hgn
     - Assignment of heat generation units to heat nodes
     - :math:`hgn(hg,n)`
   * - tes
     - Thermal energy storage units
     - :math:`tes \in hg`
   * - hb
     - Heat boilers
     - :math:`hb \in hg`
   * - wh
     - Waste heat unit
     - :math:`wh \in hg`

Parameters
~~~~~~~~~~
These parameters are initialied based on the input data give in the scenario.


.. list-table:: Model parameters
   :widths: 20 40 20 20
   :header-rows: 1

   * - **Name**
     - **Meaning**
     - **Symbol**
     - **Units**
   * - pCostHNS
     - Cost of heat not supplied
     - :math:`C^{\text{HNS}}`
     - €/kWh
   * - pCostPumping
     - Pumping cost
     - :math:`C^{\text{pump}}`
     - €/m³
   * - pTsupply
     - Supply temperature
     - :math:`T^{\text{sup}}`
     - °C
   * - pTreturn
     - Return temperature
     - :math:`T^{\text{ret}}`
     - °C
   * - pPipeCostIni
     - Initial pipe cost
     - :math:`C^{\text{pipe}}_0`
     - €
   * - pPipeCostSlope
     - Pipe cost slope
     - :math:`C^{\text{pipe}}_\text{slope}`
     - €/m m³/h
   * - pMassFlowIni
     - Initial mass flow
     - :math:`\dot{m}^{\text{ini}}`
     - m³/h
   * - pAllowDoubleHeating
     - Allow double heating
     - :math:`\delta^{\text{DoubH}}`
     - 0/1
   * - pTESlosses
     - Thermal energy storage losses
     - :math:`\eta^{\text{loss}}_{\text{TES}}`
     - -
   * - pCWater
     - Specific heat capacity of water
     - :math:`c_p^{\text{water}}`
     - kWh/(kg·K)
   * - pHeatDemand
     - Heat demand per heat node and hour
     - :math:`D_{n,h}`
     - kWh
   * - pCostDHConnect
     - Cost of DH connection per node
     - :math:`C^{\text{DH}}_n`
     - €
   * - pMaxDHPower
     - Max district heating power per node
     - :math:`P^{\max}_{n}`
     - kW
   * - pCostLocalHeatProd
     - Cost of local heat production
     - :math:`C^{\text{local}}_n`
     - €/kWh
   * - pPipeLength
     - Length of pipe connection
     - :math:`L_{n,m}`
     - m
   * - pMaxWHMF
     - Max waste heat mass flow
     - :math:`\dot{m}^{\max}_{hg,t}`
     - m³/h
   * - pCostCentrHeatProd
     - Cost of central heat production
     - :math:`C^{\text{centr}}_{hg}`
     - €/kWh
   * - pCostCentralHeatProdInv
     - Invest. cost for central heat production
     - :math:`C^{\text{centr,inv}}_{hg}`
     - €/kW
   * - pCostTESInv
     - Investment cost for TES
     - :math:`C^{\text{TES,inv}}_{tes}`
     - €/kWh

Variables
~~~~~~~~~

.. list-table:: Model variables
   :widths: 20 40 20 20
   :header-rows: 1

   * - **Name**
     - **Meaning**
     - **Symbol**
     - **Units**
   * - vMF
     - Mass flow in pipes
     - :math:`\dot{m}_{n,m,h}`
     - m³/h
   * - vHNS
     - Non supplied heat to thermal nodes
     - :math:`q^\text{HNS}_{n,h}`
     - kWh
   * - vLocalHeatProd
     - Locally produced heat
     - :math:`q^{\text{local}}_{n,h}`
     - kWh
   * - vMFConsumption
     - Mass flow consumed at node
     - :math:`\dot{m}^{\text{cons}}_{n,h}`
     - m³/h
   * - vMFInjection
     - Mass flow injected at node
     - :math:`\dot{m}^{\text{inj}}_{n,h}`
     - m³/h
   * - vCentralHeatProd
     - Central heat production
     - :math:`q^{\text{centr}}_{hg,h}`
     - kWh
   * - vCentralHeatProdInv
     - Investment in central heat production
     - :math:`x^{\text{centr}}_{hg}`
     - kW
   * - vTESLevel
     - TES level
     - :math:`e_{tes,h}`
     - m³
   * - vTESCharge
     - TES charging
     - :math:`\dot{m}^{\text{ch}}_{tes,h}`
     - m³/h
   * - vTESDischarge
     - TES discharging
     - :math:`\dot{m}^{\text{dch}}_{tes,h}`
     - m³/h
   * - vTESCapacitivInv
     - Investment in TES capacity
     - :math:`x^{\text{TES}}_{tes}`
     - m³
   * - vPipeMassFlowInv
     - Investment in pipe mass flow capacity
     - :math:`x^{\text{pipe}}_{n,m}`
     - m³/h
   * - vDHconnect
     - Investment in district heat connection
     - :math:`z^{\text{DH}}_n`
     - binary
   * - vBinBuildPipe
     - Build pipe decision
     - :math:`z^{\text{pipe}}_{n,m}`
     - binary

Objective Function
~~~~~~~~~~~~~~~~~~
**Hourly Cost Expression**

The hourly costs function hOF:sub:`h` is used to sum up all hourly contributions to the total costs, i.e. operational costs. 

.. math::

   \text{hOF}_h =
   \sum_{n} C^{\text{HNS}} \cdot q^\text{HNS}_{n,h} +
   \sum_{n} C^{\text{local}}_n \cdot q^{\text{local}}_{n,h} +
   \sum_{hg} C^{\text{centr}}_{hg} \cdot q^{\text{centr}}_{hg,h} + \\
   \sum_{(n,m) \in pc} C^{\text{pump}} \cdot L_{n,m} \cdot \dot{m}_{n,m,h}
   \quad \forall h

**Total Cost Objective**

The objective of the optimisation model is to minimize all operational and investment costs. 

.. math::

   \min \left(
   \sum_{n} C^{\text{DH}}_n \cdot z^{\text{DH}}_n +
   \sum_{hg} C^{\text{centr,inv}}_{hg} \cdot x^{\text{centr}}_{hg} +
   \sum_{tes} C^{\text{TES,inv}}_{tes} \cdot x^{\text{TES}}_{tes} + \right. \\
   \left.
   \sum_{(n,m) \in pc} C^{\text{pipe}}_0 \cdot L_{n,m} \cdot z^{\text{pipe}}_{n,m} +
   \sum_{(n,m) \in pc} C^{\text{pipe}}_\text{slope} \cdot L_{n,m} \cdot x^{\text{pipe}}_{n,m} +
   \sum_{h} \text{hOF}_h
   \right)

Constraints
~~~~~~~~~~~

**Energy Balance**

The energy balance for every hour ensures that the demand minus local heating has to be supplied by a mass flow consumption from the network. 

.. math::

   \dot{m}^{\text{cons}}_{n,h} =
   \frac{D_{n,h} - q^{\text{local}}_{n,h}}{(T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}}
   \quad \forall n,h

**Max District Heating Power**

A consumption from the district heating network is only possible when the connection is established. 

.. math::

   \dot{m}^{\text{cons}}_{n,h} \leq
   z^{\text{DH}}_n \cdot \frac{P^{\max}_n}{(T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}}
   \quad \forall n,h

**Mass Flow Balance**

The total mass flow in every node must be conserved. 

.. math::

   \dot{m}^{\text{cons}}_{n,h} +
   \sum_{hgn(tes,n)} \dot{m}^{\text{ch}}_{tes,h}
   - \dot{m}^{\text{inj}}_{n,h} =
   \sum_{(m,n) \in pc} \dot{m}_{m,n,h} -
   \sum_{(n,m) \in pc} \dot{m}_{n,m,h}
   \quad \forall n,h

**Mass Flow Injection**

The mass flow injection to the network equals the sum of all generation unit mass flows at this node. 

.. math::

   \dot{m}^{\text{inj}}_{n,h} =
   \sum_{hgn(wh,n)} \frac{q^{\text{centr}}_{wh,h}}{(T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}} +
   \sum_{hgn(hb,n)} \frac{q^{\text{centr}}_{hb,h}}{(T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}} + \\
   \sum_{hgn(tes,n)} \frac{q^{\text{centr}}_{tes,h}}{(T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}}
   \quad \forall n,h

**Local Production Exclusion (No Double Heating)**

If no double heating is allowed ($\delta^{DoubH} = 0$) all nodes which are connected to the district heating network cannot provide local heating anymore. 

.. math::

   q^{\text{local}}_{n,h} \leq
   (1 - z^{\text{DH}}_n) \cdot P^{\max}_n
   \quad \text{if } \delta^{DoubH} = 0
   \quad \forall n,h

**Max Waste Heat Power**

The available waste heat mass flow in every time step limits the heat power output for waste heat units. 

.. math::

   q^{\text{centr}}_{wh,h} \leq
   \dot{m}^{\max}_{wh,h} \cdot (T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}
   \quad \forall wh,h

**TES Discharge Conversion**

Defines the heat power output of TES from the mass flow. 

.. math::

   q^{\text{centr}}_{tes,h} =
   \dot{m}^{\text{dch}}_{tes,h} \cdot (T^{\text{sup}} - T^{\text{ret}}) \cdot c_p^{\text{water}}
   \quad \forall tes,h

**Max Heat Generation by Investment**

Limits the heat generation of all heat generation units to the installed capacity by investments. 

.. math::

   q^{\text{centr}}_{hg,h} \leq x^{\text{centr}}_{hg}
   \quad \forall hg,h

**TES Capacity by Investment**

Limits the storage level of the TES to the invested storage size. 

.. math::

   e_{tes,h} \leq x^{\text{TES}}_{tes}
   \quad \forall tes,h

**TES Energy Balance**

Time-linking storage level constraint. 

.. math::

   e_{tes,h} =
   \begin{cases}
   e_{tes,|H|} + \dot{m}^{\text{ch}}_{tes,h} - \dot{m}^{\text{dch}}_{tes,h}, & \text{if } h = 1 \\
   e_{tes,h-1} \cdot (1 - \eta^{\text{loss}}_{\text{TES}}) + \dot{m}^{\text{ch}}_{tes,h} - \dot{m}^{\text{dch}}_{tes,h}, & \text{otherwise}
   \end{cases}
   \quad \forall tes,h

**Max Pipe Mass Flow**

Limits the mass flow through the pipes to the investments into pipes (initial investment for the smallest pipe diameter plus continuous investment for higher mass flow).

.. math::

   \dot{m}_{n,m,h} \leq z^{\text{pipe}}_{n,m} \cdot \dot{m}^{\text{ini}} + x^{\text{pipe}}_{n,m}
   \quad \forall n,m \in pc, \forall h

**Logical Pipe Mass Flow Constraint**

Restricts the increased mass flow investment (for larger diameters) to pipes where the initial investment was made. 

.. math::

   x^{\text{pipe}}_{n,m} \leq z^{\text{pipe}}_{n,m} \cdot M
