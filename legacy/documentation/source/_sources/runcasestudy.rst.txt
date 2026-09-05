.. _how-to-run-a-case-study:

Run a Case Study
----------------

To run a case study, we recommend using the Jupyter notebook ``main.ipynb``, where the full workflow is documented. Alternatively, you can call the functions from the command line or include them in a customized script.

The following steps illustrate how to run a new case study step by step.

1. Environment and Packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ensure that the environment is activated (see Section :ref:`env_setup`) and selected in Jupyter. Import the required modules:

.. code-block:: python

   import prepare_geodata
   import hd_time_series_generator
   import clustering
   import model
   import visualisation

2. Retrieve Building Data
~~~~~~~~~~~~~~~~~~~~~~~~~

First, define a name for your case study, which will be used to identify all associated data and settings:

.. code-block:: python

   case_study_name = "my_first_casestudy"

Next, specify the geographical location and extent of the case study using one of two methods:

- **(i) Named location**: Any uniquely defined region in OpenStreetMap (OSM) can be used, such as "Berlin, Germany".
- **(ii) Geoshape**: Any valid polygon can be used. For convenience, use the helper function:

  .. code-block:: python

     prepare_geodata.polygon_by_circle(lat, lon, radius)

  This returns a circular polygon around the specified center point (latitude and longitude) with a radius (in km).

Then generate the geospatial dataset:

.. code-block:: python

   gdf_buildings = prepare_geodata.generate_complete_geodataset(case_study_name, location)

This returns a GeoDataFrame containing preprocessed building data and heuristically estimated annual heat demand. The console output includes data completeness, applied assumptions, and warnings about missing values.

You can perform a preliminary plausibility check, e.g. by evaluating total demand or building height ranges. For a spatial overview:

.. code-block:: python

   visualisation.plot_HD_interactive(gdf_buildings)

This opens an interactive map showing buildings color-coded by heat demand, with tooltips.

To adapt the dataset (e.g. remove outliers), modify the file ``Building_Data.geojson`` manually.

Note: Annual demand is influenced by building parameters defined in ``Building_Typology.xlsx``. If you change this file, rerun the function above to update values.

3. Generate Heat Demand Time Series
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Before generating time series, ensure the following files are prepared for your case study:

- ``outside_temp.xlsx`` (outdoor temperatures)
- ``solar_gain.xlsx`` (solar irradiation)
- ``transition_matrix_WD.xlsx`` (weekday occupancy)
- ``transition_matrix_WE.xlsx`` (weekend occupancy)

Details are described in Section :ref:`struct_param`.

Then generate the heat demand time series:

.. code-block:: python

   df_HD_time_series = hd_time_series_generator.fast_TS_generator(case_study_name, True)

This generates time series for all buildings and stores them in ``Building_TS.csv``, which can also be reused for other analyses.

4. Cluster Data and Prepare Network
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Multiple scenarios can be created for the same building dataset. To create a new scenario, define a name:

.. code-block:: python

   clustering.perform_complete_clustering(case_study_name, scenario_name)

This creates a folder structure for the scenario and populates the ``input`` folder with editable templates:

- ``input_ParameterCosts.xlsx``: General parameters (e.g. pipe costs, temperature levels, number of clusters)
- ``input_HeatGenerationUnits.xlsx``: Investment candidates for generation units
- ``input_WasteHeatProfiles.xlsx``: Time series for available waste heat capacity

Details are described in Section :ref:`struct_param`.
Make sure to keep the format of these templates consistent. Rerunning the clustering function will apply all changes and generate input data for the optimization model.

5. Run the Optimization Model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To run the model:

.. code-block:: python

   model.run_model(case_study_name, scenario_name)

Depending on complexity, the solver might run from a few minutes to several hours. Progress will be displayed in the console.

To reduce computation time, try:

- Decreasing the number of clusters
- Increasing the MIP gap

Once completed, results are saved in the scenario's ``output`` folder. Post-processed summaries are available in the ``expost`` folder, including investment decisions, cost breakdowns, and network design.

6. Visualize the Results
~~~~~~~~~~~~~~~~~~~~~~~~

To generate standard plots:

.. code-block:: python

   visualisation.make_basic_plots(case_study_name, scenario_name,
                                   time_invervall='H', start_hour=0, duration_hours=167)

This generates and saves the following figures to the ``plots`` folder:

- **Investment decisions**: Shows connected clusters, built pipes, and invested capacities.
- **Annual energy balance**: Overview of energy contributions by technology.
- **Time-resolved energy balance**: Hourly (or weekly/monthly) profiles of heat demand and supply.

You can adjust:

- ``time_invervall``: 'H' (hourly), 'D' (daily), 'W' (weekly), or 'M' (monthly)
- ``start_hour`` and ``duration_hours``: Time window for zooming into specific periods.

