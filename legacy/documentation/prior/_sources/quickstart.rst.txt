Quickstart
==========

.. _env_setup:

Environment Setup
-----------------

Before running **[Fancy Tool Name]**, ensure that the necessary environment is prepared.

1. **Using the provided script to activate the environment**:

   Simply execute ``activate_environment_windows.bat`` from the repository. This script will automatically create and activate the environment.

2. **Manually installing the requirements**:

   To manually create and activate the environment, execute the following commands:

   .. code-block:: bash

      conda env create -f environment.yml
      conda activate [your_environment_name]

Make sure to run all scripts and functions **within the activated environment** to ensure proper functionality.

Requirements
------------

- **Python version**: >= 3.8
- **Packages**: All required packages are listed in the ``environment.yml`` file.

Running the Code
----------------

The easiest and fastest way to use the tool is via the Jupyter Notebook ``main.ipynb``.

The complete workflow is illustrated step-by-step in the notebook.  
Start by running the notebook for the **preconfigured standard location**.  
If successful, you can modify the location to your **region of interest**.

For more details on available parameters and customizations, refer to the section :ref:`struct_param`.

Using the Functions in a Python Script
--------------------------------------

If you prefer to use the modules directly in your own Python scripts, the following functions are available:

.. code-block:: python

   prepare_geodata.generate_complete_geodataset(case_study_name, location)

Generates a full geodata dataset for the specified location.

- ``location`` can either be the name of a place known to OpenStreetMap or a geopolygon.
- The resulting dataset is stored as a ``.geojson`` file in the folder named after ``case_study_name``.

.. code-block:: python

   hd_time_series_generator.fast_TS_generator(case_study_name, True)

Generates heat demand time series for all buildings in the ``buildings.geojson`` file. The time series are saved as ``.csv`` files in the corresponding ``case_study_name`` directory.

.. code-block:: python

   clustering.perform_complete_clustering(case_study_name, scenario_name)

Clusters the buildings and proposes a district heating network.
- The clustered data and network proposal are prepared for the optimization model.
- Results are saved in the folder specified by ``scenario_name``.

.. code-block:: python

   model.run_model(case_study_name, scenario_name)

Runs the complete optimization model to find the optimal heating configuration based on given options and parameters.

- Results, along with figures, are stored in the folder named after ``scenario_name``.
- For further details about available settings and adjustments, see the section :ref:`struct_param`.




