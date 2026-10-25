# job-lint — cron-for-agents sanity checker

Agent operators schedule background jobs with shell-style "it runs, it
alerts" thinking — and the failure modes are systemic but unexamined:
jobs that can never stop, retries without a ceiling, alerts that re-fire
every single run until the human mutes all of them, two jobs quietly
doing the same work.

job-lint reads a declarative description of your scheduled jobs and
flags those failure modes before they bite. Single file, stdlib only:
`joblint.py`.

## Quick start

```bash
# describe your jobs (JSON, or the small YAML subset in RULES.md)
python3 joblint.py lint my-jobs.yaml
```

Exit codes: `0` clean, `1` findings, `2` usage/parse error.

Example finding:

```
[WARN] kev-correlator: JL01 no stop condition: add stop.max_runs, stop.expires, or stop.success_condition
[WARN] kev-correlator: JL04 alert without suppression: add alert.dedupe (fire_once_per / cooldown_seconds)
```

## The schema

```yaml
jobs:
  - name: kev-portfolio-correlator
    schedule: "0 8 * * *"
    purpose: "Map new KEV entries to held tickers"
    targets: ["kev-catalog", "portfolio"]
    stop:
      max_runs: 365
    retry:
      max_attempts: 3
      backoff_seconds: 60
    alert:
      on: finding
      destination: cyber-brief-chat
      dedupe:
        fire_once_per: event_id
```

See [RULES.md](RULES.md) for the full schema and the eight rules
(JL01–JL08) with severities and rationale.

## Tests

```bash
python3 test_joblint.py   # 35 tests: each rule, YAML subset, overlap, clean-pass, explain mode
```

## explain mode

`joblint.py explain "<cron>"` pairs with the `lint` mode: when JL08
flags a schedule (or when you just want to sanity-check one before
writing the job file), it explains the expression in plain English and
shows the next runs in local time.

```bash
$ joblint.py explain "*/15 * * * *"
expression: */15 * * * *
means:      every 15 minutes
...
next runs (local time):
  2026-10-23 06:45 Fri
  2026-10-23 07:00 Fri

$ joblint.py explain "0 0 30 2 *"
...
warning: this schedule never runs (no match in the next 2 years —
check impossible dates like Feb 30)
```

Accepts 5-field cron plus `@hourly/@daily/@weekly/@monthly/@yearly`,
month/day names (`mon`, `jan`), lists, ranges, and steps. Honors
standard cron day-of-month/day-of-week OR semantics and warns when both
are restricted. `--next N` (1–100, default 3) and `--json` supported.
Exit 0 explained, 2 invalid expression. Deterministic, stdlib-only —
no dateutil, no network, no dependency to install for the agent that
needs the answer.

## Limitations

- The built-in YAML parser is a deliberate subset (mappings, lists,
  scalars) — complex YAML needs converting to JSON first.
- JL06 overlap is a heuristic (target tokens + purpose similarity), not a
  semantic diff; it errs toward "worth a look."
- Schedule analysis is coarse (cadence classes, not full cron semantics).

## License

MIT — see [LICENSE](LICENSE).
