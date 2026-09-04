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

> **Competition-branch status:** the V3 draft synthetic case now runs the
> central, distributed, and hybrid modes through one new Pyomo core, including
> CRF cost, operating carbon, storage, three pipe levels, Pareto export, and
> independent QA. Guanggu v0.2 can now be audited and converted automatically;
> its V0 smoke path uses a deterministic load-centre site and Euclidean MST.
> That provisional geometry is not road-constrained. Formal performance,
> road-network, full-season model wiring and solver QA remain release gates. The Guanggu
> v0.3 delivery can now be audited and normalized into a read-only 2160-hour
> heating-season snapshot; this is an input result, not a solver result.

The completed 62-building, 2160-hour compact no-TES run is frozen as the
`compact-fullseason-v1-r3` algorithmic baseline. Its exact source commit,
artifact hashes, eight-point frontier, station-generation rationale, and
engineering limitations are recorded in the
[Compact full-season V1/R3 freeze report](docs/releases/compact_fullseason_v1_r3/README.md).
This baseline is auditable but is not labeled as a formal engineering result.

V0 smoke command (synthetic test data only):

```powershell
python scripts/run_case.py --case tests/fixtures/v3_smoke_case --profile v0-smoke
```

Guanggu v0.2 read-only audit, automatic adaptation and new-core V0 command:

```powershell
python scripts/run_case.py `
  --delivery-root "<0821代码组交付_光谷软件园目录>" `
  --source-profile wuhan_v02 `
  --assumption-profile provisional_v0 `
  --profile v0-smoke
```

This command first validates all 394 delivered files, then deterministically
selects the eight highest-annual-load buildings and the full-park peak natural
day. Its result is a `weighted_period_test`, not a formal annual conclusion.

The public command never falls back to `model.run_model()`. Historical input
contracts must use the explicit `scripts/run_legacy_case.py` regression entry.

Guanggu v0.3 full input audit and heating-season normalization:

```powershell
python scripts/validate_inputs.py `
  --delivery-root "<0823代码组交付_光谷软件园_v0.3目录>" `
  --source-profile guanggu_v03 `
  --scope heating-season `
  --full-audit
```

This command independently reads and hashes all 412 files, validates the
62-building full-year delivery, builds the 2160-hour canonical snapshot, and
writes `C:\Users\leonl\Desktop\光谷v0.3输入校验与模型就绪状态.md`. The current
expected result is `source_validation_passed=true`,
`canonical_validation_passed=true`, `model_ready=false`, and
`solver_executed=false`. See `docs/GUANGGU_V03_INPUT_GUIDE.md` for the exact
VS Code workflow, validation scope, outputs, and release blockers.

V1.0 eligibility is checked without running or modifying a case:

```powershell
python scripts/check_v1_release_gate.py --case <formal-case> --run-dir <formal-run>
```

The current draft fixture is expected to fail this gate; that failure prevents
synthetic V0 output from being mislabeled as a formal V1.0 result.

---

## Documentation

Full documentation is available and includes:

- Step-by-step usage guide
- Folder structure and configuration
- Input template formats
- Model formulation and equations
- Description of modules and functions

Competition-branch specifications and status records:

- [Compact full-season V1/R3 frozen baseline](docs/releases/compact_fullseason_v1_r3/README.md)
- [Competition input data contract](docs/DATA_CONTRACT.md)
- [Data-interface input and correction policy](docs/INPUT_INTERFACE_GUIDE.md)
- [Data-interface development status (Chinese)](docs/DATA_INTERFACE_STATUS_CN.md)
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
