Quickstart
==========

.. _env_setup:


Requirements
------------

- **Anaconda** for package management
- **Python version**: >= 3.8
- **Packages**: All required packages are listed in the ``environment.yml`` file.

Environment Setup
-----------------

Before running **UrbanHeatOpt**, ensure that the necessary environment is prepared.

1. **Using the provided script to activate the environment**:

   Simply execute ``activate_environment_windows.bat`` from the repository. This script will automatically create and activate the environment.

2. **Manually installing the requirements**:

   To manually create and activate the environment, execute the following commands:

   .. code-block:: bash

      conda env create -f environment.yml
      conda activate urbanheatopt_env

Make sure to run all scripts and functions **within the activated environment** to ensure proper functionality.


Running the Code
----------------

The easiest and fastest way to use the tool is via the Jupyter Notebook ``main.ipynb``.

The complete workflow is illustrated step-by-step in the notebook.  
Start by running the notebook for the **preconfigured standard location**.  
If successful, you can modify the location to your **region of interest**.

For more details on available parameters and customizations, refer to the section :ref:`struct_param`.

Using the Functions in a Python Script
--------------------------------------

If you prefer to use the modules directly in your own Python scripts, you need to import the following modules:

.. code-block:: python

   import prepare_geodata
   import hd_time_series_generator
   import clustering
   import model
   import visualisation

Subsequently, you can use the functions of these modules.  
Details on their usage can be found in the documentation of the modules.
