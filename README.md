# UrbanHeatOpt

**A Software Framework for Supporting Municipal Heat Transition Planning**

---

## Purpose

UrbanHeatOpt is a software framework to support municipalities and energy planners in evaluating and designing sustainable heating concepts for urban districts. It is tailored for early-stage planning and comparative analysis of scenarios with limited available data.

Key functionalities include:

- Automatic retrieval and preprocessing of building data from OpenStreetMap
- Generation of stochastic hourly heat demand time series
- Clustering of buildings and district heating network proposal
- Mixed-integer optimization of system configuration and operation
- Visualization of investment decisions, energy balances, and time profiles
- Scenario-based structure for input, output, and result comparison

---

## Quick Start: Installation and Use

Using the software does not require expert programming knowledge.

1. **Clone this repository** to your working directory.
2. **Create and verify the environment** (Anaconda or Miniconda is required):
   ```bash
   conda env create -f environment.yml
   conda activate urbanheatopt_env
   python scripts/check_environment.py
   ```
   For an already-created environment, the check can also be run without shell
   activation:
   ```bash
   conda run --no-capture-output -n urbanheatopt_env python scripts/check_environment.py
   ```

   `--no-capture-output` avoids a Conda 25.11 encoding error when this check
   prints Chinese diagnostics in a Windows GBK console.

   The checked-in `activate_environment_windows.bat` and
   `activate_environment_unix.sh` wrappers depend on the
   `Conda-Activation-Scripts` submodule. If that submodule has not been
   initialized, use the direct Conda commands above.
3. **Open the `main.ipynb` notebook** in a Jupyter-compatible environment.
4. Follow the notebook instructions to:
   - Prepare or modify a case study
   - Generate input data
   - Run clustering and optimization
   - Visualize and evaluate results

> All major functionalities can also be called directly from the Python modules.

> **Competition-branch status:** the environment check is available now. The
> standardized competition input validator and the `scripts/run_case.py`
> one-command pipeline are separate P0 tasks and are not claimed as implemented
> by this installation section.

---

## Documentation

Full documentation is available and includes:

- Step-by-step usage guide
- Folder structure and configuration
- Input template formats
- Model formulation and equations
- Description of modules and functions

Competition-branch specifications and status records:

- [Competition input data contract](docs/DATA_CONTRACT.md)
- [Model assumptions and legacy boundaries](docs/MODEL_ASSUMPTIONS.md)
- [Verified development environment](docs/ENVIRONMENT_REPORT.md)
- [Deferred real-data work](docs/P0_DEFERRED_REAL_DATA.md)
- [Questions for the load team](docs/questions_for_load_team.md)
- [Parameters awaiting project-team confirmation](docs/questions_for_project_team.md)

Visit the documentation for details:  
**[iee-tugraz.github.io/UrbanHeatOpt/](https://iee-tugraz.github.io/UrbanHeatOpt/)**

---

## License

This project is distributed under the MIT License.
