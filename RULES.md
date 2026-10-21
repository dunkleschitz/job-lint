# job-lint — rule design

Scheduled-agent-job linter. Input is a declarative description of the
agent's scheduled jobs (JSON, or a small YAML subset parsed internally —
no dependencies). Output: findings that name concrete failure modes
before they spam you or spin forever.

## Job schema (v1)

```yaml
jobs:
  - name: kev-portfolio-correlator     # required, unique
    schedule: "0 8 * * *"              # required: cron-ish string, or "hourly" etc.
    purpose: "Map new KEV entries to held tickers"   # required
    targets: ["kev-catalog", "portfolio"]  # optional: what the job reads/writes

    stop:                               # optional block — its ABSENCE is flagged
      max_runs: 100                     # run at most N times
      # expires: "2027-01-01"           # or stop after this date
      # success_condition: "..."        # or stop when this is true

    retry:                              # optional block
      max_attempts: 3                   # missing => unbounded
      backoff_seconds: 60               # missing => no backoff

    alert:                              # optional block — alerting semantics
      on: "finding"                     # what triggers it: finding | always | error
      destination: "cyber-brief-chat"   # where it goes; missing => nowhere
      dedupe:                           # optional — absence is flagged
        fire_once_per: "event_id"       # or "change", or cooldown_seconds
```

Everything else is ignored (forward-compatible).

## Rules (v1, each with id, severity, rationale)

| id | severity | name | fires when |
|---|---|---|---|
| JL01 | warn | no-stop-condition | `stop` block absent or empty. A scheduled job with no way to end is how you get a cron job still running in 2029 nobody remembers. |
| JL02 | error | unbounded-retries | `retry` present without `max_attempts`, or `max_attempts` > 10. Retry-without-ceiling is the classic self-inflicted rate-limit loop. |
| JL03 | warn | no-retry-backoff | `retry` present with `max_attempts` but no `backoff_seconds`. |
| JL04 | warn | alert-no-suppression | `alert.on` is `always` or `finding` but no `alert.dedupe`. Alerts that re-fire every run train the human to ignore all of them. |
| JL05 | warn | alert-no-destination | `alert` present without `destination`. An alert that goes nowhere is a silent failure. |
| JL06 | info | overlapping-jobs | two jobs share ≥2 target tokens or purpose-token Jaccard ≥ 0.35 while schedules are in the same cadence class (same cron minute/hour pattern class). Duplicate coverage → duplicate alerts. |
| JL07 | warn | tight-schedule-no-timeout | schedule cadence < 15 min and no `timeout`/`stop` bounding. Minute-level polling with no runtime bound stacks overlapping runs. |
| JL08 | error | invalid-schedule | schedule string parses to neither cron-5, `@hourly`-style, nor a known word. |

Severity: error = will bite you; warn = probably will; info = check it.

JL06/JL07 cadence classes: cron-5 → class by frequency estimate
(every-N-min, hourly, daily, weekly, other); words hourly/daily/weekly map
accordingly. Frequency estimates are coarse — the rule text says so.

## Exit codes

- `0` — no findings
- `1` — one or more findings (errors or warns; info-only counts as 1 too,
  so CI can gate on silence)
- `2` — usage error / input did not parse
