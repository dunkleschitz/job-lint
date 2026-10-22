#!/usr/bin/env python3
"""job-lint — sanity-check a declarative description of scheduled agent jobs.

Reads a JSON file, or a small YAML subset (mappings, lists, scalars —
parsed internally, no dependencies), describing scheduled agent jobs,
and reports concrete failure modes: jobs that can never stop, unbounded
retries, alerts without suppression, overlapping coverage, and bad
schedules.

Exit codes: 0 = no findings, 1 = findings, 2 = usage/input error.

See RULES.md for the schema and rule definitions.
"""

import json
import re
import sys
from collections import Counter

RULES = {
    "JL01": ("warn", "no stop condition: add stop.max_runs, stop.expires, or stop.success_condition"),
    "JL02": ("error", "unbounded retries: set retry.max_attempts (<= 10)"),
    "JL03": ("warn", "retry without backoff: add retry.backoff_seconds"),
    "JL04": ("warn", "alert without suppression: add alert.dedupe (fire_once_per / cooldown_seconds)"),
    "JL05": ("warn", "alert with no destination: add alert.destination"),
    "JL06": ("info", "possible duplicate coverage with another job"),
    "JL07": ("warn", "schedule tighter than 15 min with no runtime bound: add stop.max_runs or a timeout"),
    "JL08": ("error", "unparseable schedule"),
}

# ---------------------------------------------------------------- YAML subset

def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    low = text.lower()
    if low in ("null", "~", ""):
        return None
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if text.startswith("[") and text.endswith("]"):  # inline flow list
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(p) for p in inner.split(",")]
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def parse_yaml_subset(text):
    """Parse a minimal YAML subset: nested mappings by indentation,
    lists of scalars or mappings under '- ' items. Comments (#) stripped."""
    lines = []
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0]
        if stripped.strip():
            lines.append((len(raw) - len(raw.lstrip(" ")), stripped.strip()))
    if not lines:
        return {}
    pos = [0]

    def parse_block(indent):
        items = []   # for list
        mapping = {} # for map
        is_list = None
        while pos[0] < len(lines):
            ind, content = lines[pos[0]]
            if ind < indent:
                break
            if ind > indent:
                raise ValueError(f"bad indentation near: {content!r}")
            if content.startswith("- "):
                if is_list is False:
                    raise ValueError(f"mixed list and mapping near: {content!r}")
                is_list = True
                pos[0] += 1
                rest = content[2:].strip()
                if rest and ":" in rest and not rest.startswith(("[", "{")):
                    # list item that is a mapping: '- key: value'
                    key, _, val = rest.partition(":")
                    sub = {key.strip(): _scalar(val)}
                    if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                        sub.update(parse_block(lines[pos[0]][0]))
                    items.append(sub)
                else:
                    items.append(_scalar(rest))
            else:
                if is_list is True:
                    raise ValueError(f"mixed list and mapping near: {content!r}")
                is_list = False
                key, _, val = content.partition(":")
                key = key.strip()
                pos[0] += 1
                if val.strip():
                    mapping[key] = _scalar(val)
                else:
                    if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                        mapping[key] = parse_block(lines[pos[0]][0])
                    else:
                        mapping[key] = None
        return items if is_list else mapping

    return parse_block(lines[0][0])


def load_jobs(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    # Try JSON first (strict), then the YAML subset.
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = parse_yaml_subset(text)
        except ValueError as exc:
            return None, f"could not parse {path} as JSON or YAML-subset: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return None, f"{path}: top-level must be a mapping with a 'jobs' list"
    return data["jobs"], None

# ------------------------------------------------------------ schedule utils

CRON_WORDS = {"@hourly": "hourly", "@daily": "daily", "@weekly": "weekly"}
FREQ_WORDS = {"minutely": 1, "hourly": 60, "daily": 1440, "weekly": 10080}


def schedule_class(schedule):
    """Return (class, minutes-per-run estimate) or (None, None)."""
    if not isinstance(schedule, str):
        return None, None
    s = schedule.strip()
    if s in CRON_WORDS:
        return CRON_WORDS[s], {"hourly": 60, "daily": 1440, "weekly": 10080}[CRON_WORDS[s]]
    low = s.lower()
    if low in FREQ_WORDS:
        return low, FREQ_WORDS[low]
    fields = s.split()
    if len(fields) == 5 and all(re.fullmatch(r"[\d*\-/,]+", f) for f in fields):
        minute, hour = fields[0], fields[1]
        if minute == "*" and hour == "*":
            return "every-minute", 1
        if minute.startswith("*/"):
            try:
                return "every-n-min", int(minute[2:])
            except ValueError:
                return None, None
        if minute.isdigit() and hour == "*":
            return "hourly", 60
        if minute.isdigit() and hour.isdigit():
            if fields[4] != "*":
                return "weekly", 10080
            return "daily", 1440
        return "cron-other", None
    return None, None


def tokens(text):
    return set(re.findall(r"[a-z0-9]{3,}", str(text).lower()))


# ------------------------------------------------------------------- rules

def _finding(rule, job, detail=""):
    sev, desc = RULES[rule]
    msg = f"[{sev.upper()}] {job}: {rule} {desc}"
    if detail:
        msg += f" — {detail}"
    return (rule, sev, msg)


def lint_jobs(jobs):
    findings = []
    names = [j.get("name") for j in jobs if isinstance(j, dict)]
    dupes = {n for n, c in Counter(names).items() if n and c > 1}
    for name in dupes:
        findings.append(("JL00", "error", f"[ERROR] {name}: duplicate job name"))

    for job in jobs:
        if not isinstance(job, dict):
            findings.append(("JL00", "error", "[ERROR] <unnamed>: job entry is not a mapping"))
            continue
        name = job.get("name") or "<unnamed>"
        if not job.get("name"):
            findings.append(("JL00", "error", "[ERROR] <unnamed>: missing required 'name'"))
        if not job.get("schedule"):
            findings.append(("JL00", "error", f"[ERROR] {name}: missing required 'schedule'"))
        if not job.get("purpose"):
            findings.append(("JL00", "warn", f"[WARN] {name}: missing 'purpose' — overlap detection needs it"))

        # JL08: schedule parses
        cls, minutes = schedule_class(job.get("schedule"))
        if job.get("schedule") and cls is None:
            findings.append(_finding("JL08", name, f"{job.get('schedule')!r}"))

        # JL01: stop condition
        stop = job.get("stop") or {}
        if not isinstance(stop, dict):
            stop = {}
        if not any(stop.get(k) for k in ("max_runs", "expires", "success_condition")):
            findings.append(_finding("JL01", name))

        # JL02/JL03: retry bounds
        retry = job.get("retry")
        if retry is not None:
            if not isinstance(retry, dict):
                retry = {}
            max_attempts = retry.get("max_attempts")
            if not isinstance(max_attempts, int) or max_attempts > 10 or max_attempts < 1:
                findings.append(_finding("JL02", name, f"max_attempts={max_attempts!r}"))
            elif not retry.get("backoff_seconds"):
                findings.append(_finding("JL03", name))

        # JL04/JL05: alert semantics
        alert = job.get("alert")
        if alert is not None:
            if not isinstance(alert, dict):
                alert = {}
            on = str(alert.get("on", "finding")).lower()
            dedupe = alert.get("dedupe")
            if on in ("always", "finding") and not dedupe:
                findings.append(_finding("JL04", name, f"on={on!r}"))
            if not alert.get("destination"):
                findings.append(_finding("JL05", name))

        # JL07: tight schedule without bound
        if minutes is not None and minutes < 15:
            if not stop.get("max_runs") and not job.get("timeout"):
                findings.append(_finding("JL07", name, f"every ~{minutes} min"))

    # JL06: pairwise overlap — same cadence class, similar targets/purpose
    for i in range(len(jobs)):
        for k in range(i + 1, len(jobs)):
            a, b = jobs[i], jobs[k]
            if not (isinstance(a, dict) and isinstance(b, dict)):
                continue
            na, nb = a.get("name") or f"job#{i}", b.get("name") or f"job#{k}"
            ca, _ = schedule_class(a.get("schedule"))
            cb, _ = schedule_class(b.get("schedule"))
            if not ca or ca != cb:
                continue
            ta = tokens(" ".join(map(str, a.get("targets") or [])))
            tb = tokens(" ".join(map(str, b.get("targets") or [])))
            shared = ta & tb
            pa, pb = tokens(a.get("purpose") or ""), tokens(b.get("purpose") or "")
            jac = len(pa & pb) / len(pa | pb) if (pa | pb) else 0.0
            if len(shared) >= 2 or jac >= 0.35:
                detail = f"shared targets {sorted(shared)}" if len(shared) >= 2 \
                    else f"purpose similarity {jac:.2f}"
                findings.append(_finding("JL06", f"{na} <-> {nb}", detail))

    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f[1], 3), f[0], f[2]))
    return findings

# --------------------------------------------------------------------- CLI

def print_help():
    print("usage: joblint.py lint <jobs.yaml|jobs.json>")
    print()
    print("Lint a declarative scheduled-job description for the common")
    print("ways agent cron jobs go wrong. See RULES.md for the full rule list.")
    print()
    print("Exit codes: 0 = no findings, 1 = findings, 2 = usage/input error.")
    print("  -h, --help   print this help and exit")


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print_help()
        return 0
    if len(argv) != 2 or argv[0] != "lint":
        print("usage: joblint.py lint <jobs.yaml|jobs.json>", file=sys.stderr)
        return 2
    jobs, err = load_jobs(argv[1])
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    findings = lint_jobs(jobs)
    for _, _, msg in findings:
        print(msg)
    if findings:
        errs = sum(1 for f in findings if f[1] == "error")
        warns = sum(1 for f in findings if f[1] == "warn")
        print(f"\n{len(findings)} finding(s): {errs} error, {warns} warn", file=sys.stderr)
        return 1
    print("clean: no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
