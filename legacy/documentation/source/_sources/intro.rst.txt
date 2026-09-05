Introduction
============

Overview
--------

Welcome to UrbanHeatOpt!

**UrbanHeatOpt** is designed to support heat energy planning at the community level using publically available data sources.  
It provides a flexible framework for estimating heat demand, generating detailed heat demand time series, and evaluating the potential for waste heat utilization through district heating networks using linear optimization.

Main Features
~~~~~~~~~~~~~

- **Stochastic heat demand modeling**: Generates building-level heat demand time series using probabilistic models.
- **District heating network proposals**: Offers clustering and greenfield network planning based on spatial and energy criteria.
- **Waste heat utilization assessment**: Evaluates the feasibility of integrating local waste heat sources.

General Framework
~~~~~~~~~~~~~~~~~~

The tool is structured into four modular components:

1. **Prepare geodata**  
   Accesses building geometry and attributes from OpenStreetMap (OS and estimates annual heating demands based on building typologies.

2. **Generate heat demand time series**  
   Calculates dynamic heat demand profiles through thermal modeling combined with active occupancy simulations.

3. **Cluster heat demand and propose networks**  
   Performs spatial clustering of buildings to suggest candidate district heating networks, considering both technical and spatial factors.

4. **Optimize waste heat utilization**  
   Applies a linear optimization model to maximize the utilization of available waste heat sources, balancing investment and operational costs.
   
This datapipeline can be displayed as follows:

.. raw:: html

   <div style="text-align: center;">

.. mermaid::

   flowchart TD
      A["OSM Data"] --> B["1 Prepare Geodata"]
      B --> C["2 Generate Heat Demand"]
      C --> D["3 Cluster + Prop. Network"]
      D --> E["4 Optimization Model"]

.. raw:: html

   </div>
   
Each module is designed to be modular and flexible.  
All intermediate results are stored in easily accessible and standardized data formats, allowing users to leverage the outputs for other applications and custom workflows.

For detailed technical information, please refer to the respective sections.




