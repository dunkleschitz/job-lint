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
python3 test_joblint.py   # 11 tests: each rule, YAML subset, overlap, clean-pass
```

## Limitations

- The built-in YAML parser is a deliberate subset (mappings, lists,
  scalars) — complex YAML needs converting to JSON first.
- JL06 overlap is a heuristic (target tokens + purpose similarity), not a
  semantic diff; it errs toward "worth a look."
- Schedule analysis is coarse (cadence classes, not full cron semantics).

## Pre-publish checklist

Per the project charter, nothing ships publicly until the maintainer
approves the publishing account/org and the license. Before
first publication:

1. **Secrets hygiene** — run a secrets scan over the repo and its history
   (`gitleaks detect --source .` or `trufflehog git file://.`), resolve
   every hit, and grep once more for obvious keys/tokens by hand.
2. **History review** — read `git log` and the full diff end-to-end; no
   credentials, private paths, or personal data may be committed.
3. **License** — replace the TBD notice with the maintainer-approved
   LICENSE file.
4. **Approval** — get the maintainer's go-ahead for this project's first
   public publication.

## License

License TBD — awaiting project owner's approval (MIT proposed).
