# Minimal synthetic case

This directory contains the checked-in P0 smoke-test case for the competition
branch.

- Data class: `synthetic_test`
- Data version: `synthetic-v1`
- Scale: 4 buildings, 24 hourly timesteps
- Technologies: 1 synthetic fixed heat source
- Storage: disabled
- Waste heat: disabled

The files are intentionally small enough to commit to Git. They are only for
input-contract validation, adapter checks, and minimal end-to-end smoke tests.
They are not Wuhan, Guanggu, or Fehring real-data inputs.
