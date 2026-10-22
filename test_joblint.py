#!/usr/bin/env python3
"""Tests for joblint.py — one test per rule, plus parser and clean-pass."""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from joblint import lint_jobs, parse_yaml_subset, load_jobs, main

CLEAN_JOB = {
    "name": "good-job",
    "schedule": "0 8 * * *",
    "purpose": "Check the widget supply chain for tampering",
    "targets": ["supply-chain"],
    "stop": {"max_runs": 365},
    "retry": {"max_attempts": 3, "backoff_seconds": 60},
    "alert": {"on": "finding", "destination": "ops-chat",
              "dedupe": {"fire_once_per": "event_id"}},
}

RULES_OF = lambda findings: {f[0] for f in findings}


class TestRules(unittest.TestCase):
    def test_clean_job(self):
        self.assertEqual(lint_jobs([dict(CLEAN_JOB)]), [])

    def test_jl01_no_stop(self):
        job = dict(CLEAN_JOB); del job["stop"]
        self.assertIn("JL01", RULES_OF(lint_jobs([job])))

    def test_jl01_empty_stop_dict(self):
        job = dict(CLEAN_JOB); job["stop"] = {}
        self.assertIn("JL01", RULES_OF(lint_jobs([job])))

    def test_jl02_unbounded_retries(self):
        job = dict(CLEAN_JOB); job["retry"] = {"backoff_seconds": 30}
        self.assertIn("JL02", RULES_OF(lint_jobs([job])))

    def test_jl02_excessive_retries(self):
        job = dict(CLEAN_JOB); job["retry"] = {"max_attempts": 50, "backoff_seconds": 30}
        self.assertIn("JL02", RULES_OF(lint_jobs([job])))

    def test_jl03_no_backoff(self):
        job = dict(CLEAN_JOB); job["retry"] = {"max_attempts": 3}
        rules = RULES_OF(lint_jobs([job]))
        self.assertIn("JL03", rules)
        self.assertNotIn("JL02", rules)

    def test_jl04_alert_no_suppression(self):
        job = dict(CLEAN_JOB); job["alert"] = {"on": "finding", "destination": "ops-chat"}
        self.assertIn("JL04", RULES_OF(lint_jobs([job])))

    def test_jl05_alert_no_destination(self):
        job = dict(CLEAN_JOB); job["alert"] = {"on": "finding", "dedupe": {"fire_once_per": "x"}}
        self.assertIn("JL05", RULES_OF(lint_jobs([job])))

    def test_jl06_overlapping_jobs(self):
        a = dict(CLEAN_JOB, name="kev-a", purpose="Map new KEV entries to held tickers",
                 targets=["kev-catalog", "portfolio"], schedule="0 8 * * *")
        b = dict(CLEAN_JOB, name="kev-b", purpose="Match fresh KEV catalogue items against portfolio",
                 targets=["kev-catalog", "portfolio"], schedule="0 8 * * *")
        self.assertIn("JL06", RULES_OF(lint_jobs([a, b])))

    def test_jl06_no_false_positive_different_cadence(self):
        a = dict(CLEAN_JOB, name="kev-a", targets=["kev-catalog", "portfolio"], schedule="0 8 * * *")
        b = dict(CLEAN_JOB, name="kev-b", targets=["kev-catalog", "portfolio"], schedule="0 8 * * 0")
        self.assertNotIn("JL06", RULES_OF(lint_jobs([a, b])))

    def test_jl07_tight_schedule_no_bound(self):
        job = dict(CLEAN_JOB); job["schedule"] = "*/5 * * * *"; del job["stop"]
        self.assertIn("JL07", RULES_OF(lint_jobs([job])))

    def test_jl07_timeout_satisfies(self):
        job = dict(CLEAN_JOB); job["schedule"] = "*/5 * * * *"; job["timeout"] = 300
        rules = RULES_OF(lint_jobs([job]))
        self.assertNotIn("JL07", rules)

    def test_jl08_bad_schedule(self):
        job = dict(CLEAN_JOB); job["schedule"] = "whenever it feels like it"
        self.assertIn("JL08", RULES_OF(lint_jobs([job])))


class TestYamlSubset(unittest.TestCase):
    def test_nested_and_list(self):
        doc = parse_yaml_subset("""
jobs:
  - name: watch-x
    schedule: hourly
    stop:
      max_runs: 10
    targets:
      - alpha
      - beta
""")
        job = doc["jobs"][0]
        self.assertEqual(job["name"], "watch-x")
        self.assertEqual(job["stop"]["max_runs"], 10)
        self.assertEqual(job["targets"], ["alpha", "beta"])

    def test_inline_flow_list(self):
        doc = parse_yaml_subset("""
jobs:
  - name: watch-y
    schedule: daily
    targets: [alpha, beta]
""")
        self.assertEqual(doc["jobs"][0]["targets"], ["alpha", "beta"])

    def test_end_to_end_yaml_file(self):
        text = """
jobs:
  - name: noisy
    schedule: "0 8 * * *"
    purpose: "Watch things"
    alert:
      on: always
      destination: main-chat
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(text)
            path = fh.name
        try:
            jobs, err = load_jobs(path)
            self.assertIsNone(err)
            rules = RULES_OF(lint_jobs(jobs))
            self.assertIn("JL01", rules)  # no stop
            self.assertIn("JL04", rules)  # alert always, no dedupe
        finally:
            os.unlink(path)

    def test_json_input(self):
        import json
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"jobs": [CLEAN_JOB]}, fh)
            path = fh.name
        try:
            jobs, err = load_jobs(path)
            self.assertIsNone(err)
            self.assertEqual(lint_jobs(jobs), [])
        finally:
            os.unlink(path)


class TestCli(unittest.TestCase):
    def test_usage_error(self):
        self.assertEqual(main([]), 2)

    def test_help_flag(self):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = main(["--help"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("usage:", buf.getvalue())

    def test_missing_file(self):
        self.assertEqual(main(["lint", "/nonexistent/jobs.yaml"]), 2)

    def test_lint_help_flag(self):
        # `lint --help` must print help, not try to read "--help" as a file.
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = main(["lint", "--help"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("usage:", buf.getvalue())

    def test_exit_codes(self):
        import json
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"jobs": [CLEAN_JOB]}, fh)
            clean = fh.name
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            bad = dict(CLEAN_JOB); del bad["stop"]
            json.dump({"jobs": [bad]}, fh)
            dirty = fh.name
        try:
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                self.assertEqual(main(["lint", clean]), 0)
                self.assertEqual(main(["lint", dirty]), 1)
            finally:
                sys.stdout = old
        finally:
            os.unlink(clean); os.unlink(dirty)


if __name__ == "__main__":
    unittest.main(verbosity=1)
