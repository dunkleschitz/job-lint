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

VERSION = "1.1.0"

CRON_ATWORDS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *",
                "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *",
                "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
MONTH_NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,
               "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
               "nov": 11, "dec": 12}
DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4,
             "fri": 5, "sat": 6}
DOW_FULL = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday"]
MONTH_FULL = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November",
              "December"]

# (field name, min, max, name-map); dow max 7 (7 == Sunday)
CRON_FIELDS = (("minute", 0, 59, None), ("hour", 0, 23, None),
               ("day of month", 1, 31, None), ("month", 1, 12, MONTH_NAMES),
               ("day of week", 0, 7, DOW_NAMES))


def _cron_value(token, lo, hi, names):
    token = token.strip().lower()
    if names and token in names:
        return names[token]
    if token == "*":
        return None  # wildcard marker, handled by caller
    v = int(token)   # raises ValueError on junk
    if not (lo <= v <= hi):
        raise ValueError(f"value {v} out of range {lo}-{hi}")
    return v


def parse_cron_field(field, lo, hi, names):
    """Parse one cron field -> (is_star, sorted set of ints).

    Raises ValueError on anything malformed. A literal "*" (only) is a
    star; "*/n", lists, ranges, and names all become explicit sets.
    """
    field = field.strip()
    if field == "*":
        return True, set(range(lo, hi + 1))
    values = set()
    for part in field.split(","):
        part = part.strip()
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError(f"step must be >= 1 in {part!r}")
        else:
            base, step = part, 1
        if base in ("*", ""):
            start, end = lo, hi
        elif "-" in base:
            s_s, e_s = base.split("-", 1)
            start = _cron_value(s_s, lo, hi, names)
            end = _cron_value(e_s, lo, hi, names)
            if end < start:
                raise ValueError(f"reversed range {part!r}")
        else:
            start = end = _cron_value(base, lo, hi, names)
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"field {field!r} selects nothing")
    if lo == 0 and hi == 7:  # fold Sunday 7 -> 0
        values = {0 if v == 7 else v for v in values}
    return False, values


def parse_cron(expr):
    """Parse a cron expression -> dict of field info. Raises ValueError."""
    s = expr.strip()
    if s.startswith("@"):
        low = s.lower()
        if low not in CRON_ATWORDS:
            raise ValueError(f"unknown @-word {s!r}")
        s = CRON_ATWORDS[low]
    parts = s.split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields, got {len(parts)} in {expr!r}")
    fields = {}
    for (name, lo, hi, names), part in zip(CRON_FIELDS, parts):
        star, values = parse_cron_field(part, lo, hi, names)
        fields[name] = {"star": star, "values": values, "raw": part}
    return fields


def _field_english(name, info):
    vals = sorted(info["values"])
    if info["star"]:
        return {"minute": "every minute", "hour": "every hour",
                "day of month": "every day of the month",
                "month": "every month",
                "day of week": "every day of the week"}[name]
    if name == "minute":
        if len(vals) == 1:
            return f"at minute {vals[0]:02d}"
        span, step = vals[-1] - vals[0], vals[1] - vals[0]
        if vals == list(range(vals[0], vals[-1] + 1, step)):
            if vals[0] == 0 and vals[-1] == 60 - step and step > 1:
                return f"every {step} minutes"
            return f"every {step} minutes from :{vals[0]:02d} to :{vals[-1]:02d}"
        return "at minutes " + ", ".join(f"{v:02d}" for v in vals)
    if name == "hour":
        if len(vals) == 1:
            return f"at {vals[0]:02d}:00"
        return "at hours " + ", ".join(f"{v:02d}:00" for v in vals)
    if name == "day of month":
        if len(vals) == 1:
            return f"on day {vals[0]} of the month"
        return "on days " + ", ".join(str(v) for v in vals) + " of the month"
    if name == "month":
        if len(vals) == 1:
            return f"in {MONTH_FULL[vals[0]]}"
        return "in " + ", ".join(MONTH_FULL[v] for v in vals)
    if name == "day of week":
        if len(vals) == 1:
            return f"on {DOW_FULL[vals[0]]}s"
        return "on " + ", ".join(DOW_FULL[v] + "s" for v in vals)
    return name  # unreachable


def explain_cron(expr):
    """Return (plain-english dict, warnings list). Raises ValueError."""
    fields = parse_cron(expr)
    f = fields
    warnings = []
    minute, hour = f["minute"], f["hour"]
    dom, mon, dow = f["day of month"], f["month"], f["day of week"]

    # combined headline for the common shapes
    if (minute["star"] and hour["star"] and dom["star"] and mon["star"]
            and dow["star"]):
        headline = "every minute"
    elif (len(minute["values"]) == 1 and hour["star"] and dom["star"]
            and mon["star"] and dow["star"]):
        headline = f"every hour at :{sorted(minute['values'])[0]:02d}"
    elif (len(minute["values"]) == 1 and len(hour["values"]) == 1
            and dom["star"] and mon["star"] and dow["star"]):
        m, h = sorted(minute["values"])[0], sorted(hour["values"])[0]
        headline = f"daily at {h:02d}:{m:02d}"
    elif (len(minute["values"]) == 1 and len(hour["values"]) == 1
            and dom["star"] and mon["star"] and not dow["star"]):
        m, h = sorted(minute["values"])[0], sorted(hour["values"])[0]
        days = sorted(dow["values"])
        if days == [1, 2, 3, 4, 5]:
            when = "weekdays"
        elif days == [0, 6]:
            when = "weekends"
        else:
            when = "weekly (" + _field_english("day of week", dow)[3:] + ")"
        headline = f"{when} at {h:02d}:{m:02d}"
    elif (not minute["star"] and hour["star"] and dom["star"]
            and mon["star"] and dow["star"]):
        # sub-hourly cadence: the minute field says it all
        headline = _field_english("minute", minute)
    elif (len(minute["values"]) == 1 and len(hour["values"]) == 1
            and not dom["star"] and mon["star"] and dow["star"]):
        m, h = sorted(minute["values"])[0], sorted(hour["values"])[0]
        headline = (f"monthly at {h:02d}:{m:02d} "
                    f"({_field_english('day of month', dom)})")
    else:
        bits = []
        if not minute["star"] or not hour["star"]:
            mh = _field_english("minute", minute)
            hh = _field_english("hour", hour)
            bits.append(f"{mh}, {hh}")
        if not dom["star"] or not dow["star"]:
            if not dom["star"] and not dow["star"]:
                warnings.append(
                    "day-of-month AND day-of-week are both restricted: "
                    "standard cron runs when EITHER matches")
            if not dom["star"]:
                bits.append(_field_english("day of month", dom))
            if not dow["star"]:
                bits.append(_field_english("day of week", dow))
        if not mon["star"]:
            bits.append(_field_english("month", mon))
        headline = "; ".join(bits) if bits else "every minute"

    detail = {name: _field_english(name, info)
              for name, info in fields.items()}
    return {"headline": headline, "fields": detail}, warnings


def _matches(fields, dt):
    f = fields
    if dt.minute not in f["minute"]["values"]:
        return False
    if dt.hour not in f["hour"]["values"]:
        return False
    if dt.month not in f["month"]["values"]:
        return False
    dom_star, dow_star = f["day of month"]["star"], f["day of week"]["star"]
    dom_ok = dt.day in f["day of month"]["values"]
    # Python Monday=0; cron Sunday=0
    dow_ok = ((dt.weekday() + 1) % 7) in f["day of week"]["values"]
    if dom_star and dow_star:
        return True
    if dom_star:
        return dow_ok
    if dow_star:
        return dom_ok
    return dom_ok or dow_ok  # standard cron OR semantics


def next_cron_runs(expr, count=3, now=None, limit_days=730):
    """Next `count` run datetimes (local time). Raises ValueError.

    Returns (runs, never_runs_flag): never_runs_flag is True when no run
    occurs within limit_days (e.g. Feb 30).
    """
    from datetime import datetime, timedelta
    fields = parse_cron(expr)
    now = now or datetime.now()
    cand = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = now + timedelta(days=limit_days)
    runs = []
    while cand <= end and len(runs) < count:
        if _matches(fields, cand):
            runs.append(cand)
        cand += timedelta(minutes=1)
    return runs, not runs


def cmd_explain(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="joblint.py explain", add_help=False)
    ap.add_argument("expr", nargs="?")
    ap.add_argument("--next", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    try:
        ns = ap.parse_args(argv)
    except SystemExit:
        return 2
    if ns.help or not ns.expr:
        print("usage: joblint.py explain \"<cron expr>\" [--next N] [--json]")
        print()
        print("Explain a 5-field cron expression (or @hourly/@daily/@weekly/")
        print("@monthly/@yearly) in plain English and show the next runs.")
        print("Names (mon, jan) and lists/ranges/steps are accepted.")
        print()
        print("Exit codes: 0 = explained, 2 = invalid expression.")
        return 0
    if ns.next < 1 or ns.next > 100:
        print("error: --next must be 1..100", file=sys.stderr)
        return 2
    try:
        explained, warnings = explain_cron(ns.expr)
        runs, never = next_cron_runs(ns.expr, count=ns.next)
    except ValueError as exc:
        print(f"error: invalid cron expression: {exc}", file=sys.stderr)
        return 2
    if never:
        warnings.append("this schedule never runs (no match in the next "
                        "2 years — check impossible dates like Feb 30)")
    if ns.json:
        print(json.dumps({
            "expression": ns.expr,
            "headline": explained["headline"],
            "fields": explained["fields"],
            "warnings": warnings,
            "next_runs": [r.isoformat(timespec="minutes") for r in runs],
            "never_runs": never,
        }, indent=2, sort_keys=True))
        return 0
    print(f"expression: {ns.expr}")
    print(f"means:      {explained['headline']}")
    print("fields:")
    for name in ("minute", "hour", "day of month", "month", "day of week"):
        print(f"  {name:13s} {explained['fields'][name]}")
    for w in warnings:
        print(f"warning: {w}")
    if runs:
        print("next runs (local time):")
        for r in runs:
            print(f"  {r.strftime('%Y-%m-%d %H:%M %a')}")
    return 0


# ------------------------------------------------------------------- rules

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
    print("       joblint.py explain \"<cron expr>\" [--next N] [--json]")
    print()
    print("Lint a declarative scheduled-job description for the common")
    print("ways agent cron jobs go wrong. See RULES.md for the full rule list.")
    print()
    print("explain: describe a 5-field cron expression (or @hourly/@daily/")
    print("@weekly/@monthly/@yearly) in plain English and show next runs.")
    print()
    print("Exit codes: 0 = no findings / explained, 1 = findings,")
    print("            2 = usage/input error (incl. invalid cron).")
    print("  -h, --help   print this help and exit")


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print_help()
        return 0
    if argv and argv[0] == "--version":
        print("job-lint " + VERSION)
        return 0
    if argv[:1] == ["lint"] and argv[1:] in (["-h"], ["--help"]):
        print_help()
        return 0
    if argv[:1] == ["explain"]:
        return cmd_explain(argv[1:])
    if len(argv) != 2 or argv[0] != "lint":
        print("usage: joblint.py lint <jobs.yaml|jobs.json>", file=sys.stderr)
        print("       joblint.py explain \"<cron expr>\" [--next N] [--json]",
              file=sys.stderr)
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
