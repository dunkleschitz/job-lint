# job-lint CHANGELOG

## v1 — 2026-10-21

Initial build.

- Single-file stdlib CLI (`joblint.py`) linting declarative job
  descriptions: JSON, or a small YAML subset parsed internally (no deps).
- 8 rules: JL01 no-stop-condition, JL02 unbounded-retries,
  JL03 no-retry-backoff, JL04 alert-without-suppression,
  JL05 alert-no-destination, JL06 overlapping-jobs (same cadence class +
  shared targets / purpose similarity), JL07 tight-schedule-no-timeout,
  JL08 invalid-schedule.
- Exit codes 0 clean / 1 findings / 2 usage.
- `RULES.md` documents the job schema and each rule's rationale.
- 20 tests passing (`test_joblint.py`); smoke-tested on realistic jobs
  (caught the real duplicate dip-trigger pair).

## 2026-10-22

- Added this CHANGELOG (was the only bench tool missing one).
- Regression sweep (all nine tools): all suites green — quietwatch,
  sandbox-physical, memport (13/13; the VERIFY FAILED line in output is
  the tamper-detection test exercising its intended path), job-lint
  (20/20), call-log-condenser, hookbin (12/12), maildrop (11/11),
  gatekeeper (21/21), labnote (15/15).

## v1.0.1 — 2026-10-22

- `-h/--help` now prints full help and exits 0 (previously: usage line on
  stderr, exit 2).
