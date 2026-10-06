"""Answer evals: real player questions (evals/answers.json) scored against the real knowledge base, for accuracy,
completeness and speed on every AI provider.

    python tools/eval_answers.py                                   # quick mode: instant answers only, free, seconds
    python tools/eval_answers.py --provider claude [--model sonnet] [--cases list,shop-*] [--workers 2]
                                 [--repeat 3] [--profile "level=35,job=Hunter,base_class=Bowman"] [--yes]
    python tools/eval_answers.py --provider all-signed-in --yes     # every AI CLI installed and signed in here
    python tools/eval_answers.py --compare evals/reports/A.json evals/reports/B.json

Quick mode checks maplehelper/quick.py: every case with "instant" set must be answered instantly and correctly
(instant: true) or left to the AI (instant: false). It needs no network and fails (exit 1) on any miss, so it
runs in pytest too.

Live mode (--provider, or the old --mode claude) sends each question through the real Brain, i.e. the AI CLI on
YOUR account: every case spends plan usage. It is the release gate for changes to prompts, brain.py, the KB tables
or a provider, never for CI. Each answer is scored (checks, list recall) and timed (total seconds, time to the first
visible text, the tool calls and turns the CLI reports). Results go to evals/reports/<timestamp>-<provider>.json
(gitignored) and are compared with that provider's previous report: an answer that passed there and fails now is
a regression (exit 1). --compare prints the per-case deltas between any two reports (before/after a change).

--workers N runs N questions at once (one AI process each): faster, but the CLIs then compete for CPU and the
provider's rate limits, so seconds are not comparable with a --workers 1 report. Use 1 for timing baselines.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import queue
import re
import shutil
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = ROOT / "evals" / "answers.json"
REPORTS = ROOT / "evals" / "reports"
KB = ROOT / "data" / "kb"            # the repo's KB, not a downloaded update: results must be reproducible

LANGS = {"he", "en"}
KINDS = {"stats", "drops", "who_drops", "where", "npc", "quest", "job", "training", "guide", "judgement", "screenshot",
         "list", "equip", "shop", "craft", "compare", "skills"}
CHECKS = {"must_mention", "must_mention_any", "must_not_mention", "entities_include", "instant",
          "must_list", "must_not_list"}
FIELDS = {"id", "question", "lang", "kind", "checks", "note", "why", "tags", "profile"}
PROFILE_FIELDS = {"name": str, "base_class": str, "job": str, "level": int}
# the character every live question is asked as, unless the case or --profile says otherwise: a typical player
DEFAULT_PROFILE = {"name": "EvalThief", "base_class": "Thief", "job": "Assassin", "level": 31}
PROVIDERS = ("claude", "codex", "gemini", "grok")
ALL_SIGNED_IN = "all-signed-in"


# ------------------------------------------------------------------ cases

def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def _profile_problems(cid: str, profile) -> list[str]:
    if not isinstance(profile, dict) or not profile:
        return [f"{cid}: profile must be a non-empty object"]
    problems = [f"{cid}: unknown profile fields {sorted(set(profile) - set(PROFILE_FIELDS))}"] \
        if set(profile) - set(PROFILE_FIELDS) else []
    for k, typ in PROFILE_FIELDS.items():
        if k in profile and (not isinstance(profile[k], typ) or isinstance(profile[k], bool) or not profile[k]):
            problems.append(f"{cid}: profile {k} must be a {'number' if typ is int else 'non-empty string'}")
    if isinstance(profile.get("level"), int) and not 1 <= profile["level"] <= 250:
        problems.append(f"{cid}: profile level must be 1-250")
    return problems


def validate_cases(cases: list[dict]) -> list[str]:
    """Every problem in the case file, so a typo fails loudly instead of silently skipping a check."""
    problems, seen = [], set()
    for i, c in enumerate(cases):
        cid = c.get("id") or f"#{i}"
        if not c.get("id") or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", c["id"]):
            problems.append(f"{cid}: id must be lowercase-with-dashes")
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)
        extra = set(c) - FIELDS
        if extra:
            problems.append(f"{cid}: unknown fields {sorted(extra)}")
        if not isinstance(c.get("question"), str) or not c["question"].strip():
            problems.append(f"{cid}: empty question")
        if c.get("lang") not in LANGS:
            problems.append(f"{cid}: lang must be one of {sorted(LANGS)}")
        if c.get("kind") not in KINDS:
            problems.append(f"{cid}: kind must be one of {sorted(KINDS)}")
        tags = c.get("tags")
        if tags is not None and not (isinstance(tags, list) and tags
                                     and all(isinstance(t, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", t) for t in tags)):
            problems.append(f"{cid}: tags must be a non-empty list of lowercase words")
        if "profile" in c:
            problems += _profile_problems(cid, c["profile"])
        if "why" in c and not (isinstance(c["why"], str) and c["why"].strip()):
            problems.append(f"{cid}: why must be a non-empty string")
        checks = c.get("checks")
        if not isinstance(checks, dict) or not checks:
            problems.append(f"{cid}: checks must be a non-empty object")
            continue
        if set(checks) - CHECKS:
            problems.append(f"{cid}: unknown checks {sorted(set(checks) - CHECKS)}")
        for name in CHECKS - {"instant"}:
            v = checks.get(name)
            if v is not None and not (isinstance(v, list) and v and all(isinstance(s, str) and s for s in v)):
                problems.append(f"{cid}: {name} must be a non-empty list of strings")
        if "instant" in checks and not isinstance(checks["instant"], bool):
            problems.append(f"{cid}: instant must be true or false")
        if checks.get("instant") is True and not (checks.get("must_mention") or checks.get("must_mention_any")):
            problems.append(f"{cid}: an instant case needs something to check in the answer")
        if (checks.get("must_list") or checks.get("must_not_list")) and not c.get("why"):
            problems.append(f"{cid}: a list case needs a \"why\": how its expected list was worked out")
    return problems


def select_cases(cases: list[dict], spec: str | None) -> list[dict]:
    """The cases a comma-separated filter names: ids, tags or id patterns ("list-*"). Unknown words fail
    (SystemExit 2), so a typo never runs (and pays for) the whole file."""
    if not spec:
        return cases
    words = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [w for w in words if not any(_matches(c, w) for c in cases)]
    if unknown:
        raise SystemExit("no case matches: " + ", ".join(unknown))
    return [c for c in cases if any(_matches(c, w) for w in words)]


def _matches(case: dict, word: str) -> bool:
    return case["id"] == word or word in (case.get("tags") or []) or case.get("kind") == word \
        or (any(ch in word for ch in "*?[") and fnmatch.fnmatchcase(case["id"], word))


def parse_profile(text: str | None) -> dict | None:
    """--profile "level=35,job=Hunter,base_class=Bowman,name=Archie" -> a profile; "none" -> {} (no profile)."""
    if text is None:
        return None
    if text.strip().lower() in ("none", "no", ""):
        return {}
    out = {}
    for part in text.split(","):
        k, sep, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or k not in PROFILE_FIELDS or not v:
            raise SystemExit(f"bad --profile part {part!r}: use level=31,job=Assassin,base_class=Thief,name=X")
        out[k] = int(v) if PROFILE_FIELDS[k] is int else v
    return out


def case_profile(case: dict, override: dict | None = None) -> dict:
    """The profile a case is asked with: --profile, else the case's own (over the default), else the default.
    {} = no profile at all."""
    if override is not None:
        return dict(DEFAULT_PROFILE, **override) if override else {}
    return dict(DEFAULT_PROFILE, **(case.get("profile") or {}))


def character(profile: dict):
    """The profile as the app's Character (in memory only: nothing is saved)."""
    if not profile:
        return None
    from maplehelper.store import Character
    return Character(id="eval", name=profile["name"], base_class=profile["base_class"], job=profile["job"],
                     level=profile["level"])


# ------------------------------------------------------------------ scoring

def _norm(s: str) -> str:
    """Case, curly apostrophes, thousands separators and no-break spaces don't matter: "7,420" is "7420", and a
    Hebrew answer's "Crimson Balrog" (kept on one line with a no-break space) is "Crimson Balrog"."""
    s = s.lower().replace("’", "'").replace(" ", " ")
    return re.sub(r"(?<=\d),(?=\d{3})", "", s)


def answer_keys(answer) -> list[str]:
    """The keys an answer shows as cards: its entities plus the "who drops it" groups."""
    keys = list(answer.entities or [])
    for g in answer.drop_groups or []:
        keys += [g.get("monster", "")] + list(g.get("items") or [])
    return [k for k in keys if k]


def haystack(answer, kb) -> str:
    """What the player sees: the text and the names on the cards (an instant drops answer lists items as cards)."""
    names = [(kb.get(k) or {}).get("name", "") for k in answer_keys(answer)]
    return _norm("\n".join([answer.text or ""] + names))


_EDGE = "A-Za-z0-9"       # a value's edges: "3000" is not in "30000", "Raffle" not in "Rafflesia" (Hebrew prefixes glue)


def _longer(kb, value: str) -> list[str]:
    """The KB names that hold this value as whole words and are longer ("Garnet Ore" for "Garnet")."""
    cache = kb.__dict__.setdefault("_eval_longer", {})
    if value not in cache:
        pat = re.compile(rf"(?<![{_EDGE}]){re.escape(value)}(?![{_EDGE}])")
        cache[value] = sorted({n for e in kb.entities.values() if (n := _norm(e.get("name") or "")) != value
                               and pat.search(n)}, key=len, reverse=True)
    return cache[value]


def _found(hay: str, value: str, kb) -> bool:
    """value is in what the player sees as itself: whole words or a whole number, and not only inside a longer KB
    name ("Garnet" is not said by "Garnet Ore", "Henesys" not by a "Return Scroll to Henesys" card). Plain
    substrings passed wrong answers and failed right ones."""
    value = _norm(value)
    covered = [m.span() for n in _longer(kb, value) if n in hay
               for m in re.finditer(rf"(?<![{_EDGE}]){re.escape(n)}(?![{_EDGE}])", hay)] if kb is not None else []
    return any(not any(a <= m.start() and m.end() <= b for a, b in covered)
               for m in re.finditer(rf"(?<![{_EDGE}]){re.escape(value)}(?![{_EDGE}])", hay))


def recall(checks: dict, answer, kb) -> float | None:
    """The share of must_list names the answer shows (None: not a list case)."""
    names = checks.get("must_list")
    if not names or answer is None:
        return None
    hay = haystack(answer, kb)
    return round(sum(_found(hay, n, kb) for n in names) / len(names), 3)


def score(checks: dict, answer, kb) -> list[str]:
    """Problems with one answer (empty = pass). Only the content checks: "instant" is the caller's business.
    must_list passes only when every name is there (recall() gives the share)."""
    hay = haystack(answer, kb)
    problems = [f"missing '{s}'" for s in checks.get("must_mention", []) if not _found(hay, s, kb)]
    anyof = checks.get("must_mention_any")
    if anyof and not any(_found(hay, s, kb) for s in anyof):
        problems.append("none of " + ", ".join(f"'{s}'" for s in anyof))
    problems += [f"mentions '{s}'" for s in checks.get("must_not_mention", []) if _found(hay, s, kb)]
    keys = set(answer_keys(answer))
    problems += [f"no card for {k}" for k in checks.get("entities_include", []) if k not in keys]
    wanted = checks.get("must_list") or []
    missing = [s for s in wanted if not _found(hay, s, kb)]
    if missing:
        problems.append(f"list misses {len(missing)}/{len(wanted)}: " + ", ".join(f"'{s}'" for s in missing))
    problems += [f"lists '{s}'" for s in checks.get("must_not_list", []) if _found(hay, s, kb)]
    return problems


def _result(case: dict, answer, problems: list[str], **extra) -> dict:
    return {"id": case["id"], "lang": case["lang"], "kind": case["kind"], "tags": case.get("tags") or [],
            "ok": not problems, "problems": problems,
            "recall": extra.pop("recall", None),
            "text": answer.text if answer else None, "entities": answer_keys(answer) if answer else [], **extra}


# ------------------------------------------------------------------ quick mode

def run_quick(cases: list[dict], kb) -> list[dict]:
    """Every case with "instant" set, against quick.answer only (no network)."""
    from maplehelper import quick
    from maplehelper.i18n import I18n

    results = []
    for c in cases:
        checks = c["checks"]
        if "instant" not in checks:
            continue
        a = quick.answer(c["question"], kb, I18n(c["lang"]))
        if not checks["instant"]:
            problems = [] if a is None else ["answered instantly, should go to the AI: " + a.text.split("\n")[0]]
        elif a is None:
            problems = ["fell through to the AI, expected an instant answer"]
        else:
            problems = score(checks, a, kb)
        results.append(_result(c, a, problems, recall=recall(checks, a, kb) if a else None))
    return results


# ------------------------------------------------------------------ live mode

def signed_in_providers() -> list[str]:
    """The AI CLIs installed and signed in on this PC (each provider's own account check: read-only)."""
    from maplehelper import providers
    out = []
    for name in PROVIDERS:
        try:
            status = providers.PROVIDERS[name].account().get("status")
        except Exception:      # noqa: BLE001 - a CLI that fails its check is simply not benchmarked
            status = None
        if status == "ok":
            out.append(name)
    return out


def scratch_kb(src: Path) -> Path:
    """A copy of the KB (without pictures) for a live run: the app writes its grep tables (drops.tsv, rewards.tsv)
    beside index.json, which must not touch data/kb, and parallel workers must not race on them."""
    dst = Path(tempfile.mkdtemp(prefix="maplehelper-eval-kb-"))
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("img"))
    return dst


def _ask(brain, case: dict, run: int, profile: dict, kb, provider: str) -> dict:
    start = time.monotonic()
    first: list[float] = []

    def on_delta(text: str):
        if not first and text.strip():
            first.append(round(time.monotonic() - start, 2))
    try:
        a = brain.ask(case["question"], character(profile), None, None, on_delta=on_delta)
    except Exception as e:      # noqa: BLE001 - one broken case must not end a paid run
        a, crash = None, f"{type(e).__name__}: {e}"
    else:
        crash = None
    secs = round(time.monotonic() - start, 2)
    if crash or a.error:
        problems = [f"error: {crash or a.error}"]
    else:
        problems = score(case["checks"], a, kb)
    return _result(case, a, problems, recall=None if crash or a.error else recall(case["checks"], a, kb),
                   provider=provider, run=run, seconds=secs,
                   # Codex doesn't stream: its first text is the whole answer
                   first_text_s=first[0] if first else None,
                   tool_calls=getattr(a, "tool_calls", None), turns=getattr(a, "turns", None),
                   cost_usd=getattr(a, "cost_usd", None), model=getattr(a, "model", None),
                   profile=profile or None)


def run_live(cases: list[dict], kb, provider: str = "claude", model: str | None = None, workers: int = 1,
             repeat: int = 1, profile: dict | None = None, on_result=None) -> list[dict]:
    """Each case (repeat times) through the real Brain on one provider, `workers` at a time, each worker with a
    Brain of its own. Costs plan usage: see the module docstring. Results come back in case order."""
    from maplehelper.brain import Brain

    brains = [Brain(kb, provider=provider, model=model) for _ in range(max(1, workers))]
    if not brains[0].available():
        for b in brains:
            b.shutdown()
        raise SystemExit(f"{provider}: its CLI was not found. Install it and sign in first.")
    free: queue.Queue = queue.Queue()
    for b in brains:
        getattr(b, "prewarm", lambda: None)()          # as the app does when the chat opens
        free.put(b)
    jobs = [(c, run) for run in range(max(1, repeat)) for c in cases]
    lock = threading.Lock()

    def job(c, run):
        b = free.get()
        try:
            r = _ask(b, c, run, case_profile(c, profile), kb, provider)
        finally:
            free.put(b)
        if on_result:
            with lock:
                on_result(r)
        return r
    try:
        with ThreadPoolExecutor(max_workers=len(brains)) as pool:
            futures = [pool.submit(job, c, run) for c, run in jobs]
            return [f.result() for f in futures]
    finally:
        for b in brains:
            b.shutdown()          # ask() leaves a pre-warmed process behind


def run_claude(cases: list[dict], kb, model: str = "sonnet", on_result=None) -> list[dict]:
    """The old Claude-only runner (one at a time, default profile)."""
    return run_live(cases, kb, "claude", model, on_result=on_result)


# ------------------------------------------------------------------ numbers

def _median(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def _p90(xs: list) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return xs[max(0, -(-len(xs) * 9 // 10) - 1)] if xs else None       # nearest rank


def per_case(results: list[dict]) -> dict[str, dict]:
    """The runs of each case (--repeat) folded into one row: the share that passed, mean recall, median times."""
    runs: dict[str, list[dict]] = {}
    for r in results:
        runs.setdefault(r["id"], []).append(r)
    out = {}
    for cid, rs in runs.items():
        recalls = [r.get("recall") for r in rs if r.get("recall") is not None]
        out[cid] = {"id": cid, "runs": len(rs), "pass": round(sum(r["ok"] for r in rs) / len(rs), 3),
                    "recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
                    "seconds": _median([r.get("seconds") for r in rs]),
                    "first_text_s": _median([r.get("first_text_s") for r in rs]),
                    "tool_calls": _median([r.get("tool_calls") for r in rs]),
                    "problems": next((r["problems"] for r in reversed(rs) if r.get("problems")), [])}
    return out


def summarize(results: list[dict]) -> dict:
    """One provider's numbers: pass rate, mean recall of the list cases, median / p90 seconds, tool calls, and
    the five worst cases (fewest passes, then lowest recall, then slowest)."""
    secs = [r.get("seconds") for r in results]
    recalls = [r["recall"] for r in results if r.get("recall") is not None]
    rows = per_case(results).values()
    worst = sorted(rows, key=lambda c: (c["pass"], c["recall"] if c["recall"] is not None else 1,
                                        -(c["seconds"] or 0)))[:5]
    return {"runs": len(results), "passed": sum(r["ok"] for r in results),
            "pass_rate": round(sum(r["ok"] for r in results) / len(results), 3) if results else None,
            "mean_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
            "median_s": _median(secs), "p90_s": _p90(secs),
            "median_first_text_s": _median([r.get("first_text_s") for r in results]),
            "median_tool_calls": _median([r.get("tool_calls") for r in results]),
            "median_turns": _median([r.get("turns") for r in results]),
            "errors": sum(any(p.startswith("error:") for p in r.get("problems") or []) for r in results),
            "worst": [{k: c[k] for k in ("id", "pass", "recall", "seconds", "tool_calls", "problems")} for c in worst]}


# ------------------------------------------------------------------ reports

def report_provider(path: Path) -> str:
    """The provider of a report by its name: "20261005-101500-codex.json"; the older "<timestamp>.json" is Claude."""
    parts = path.stem.split("-", 2)
    return parts[2] if len(parts) == 3 else "claude"


def save_report(results: list[dict], model: str | None, folder: Path = REPORTS, provider: str = "claude",
                **settings) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = base = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while any(folder.glob(f"{stamp}-*.json")):      # two runs in one second: never overwrite, keep the time order
        n += 1
        stamp = f"{base}.{n}"
    path = folder / f"{stamp}-{provider}.json"
    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "live", "provider": provider, "model": model,
              **settings, "passed": sum(r["ok"] for r in results), "total": len(results),
              "summary": summarize(results), "results": results}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def previous_report(folder: Path = REPORTS, before: Path | None = None, provider: str | None = None) -> dict | None:
    """The newest report older than `before` (names are timestamps, so they sort by time), of `provider` when
    given: a Codex run is compared with Codex's last run, never with Claude's."""
    paths = sorted(p for p in folder.glob("*.json") if (before is None or p.name < before.name)
                   and (provider is None or report_provider(p) == provider))
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def compare(previous: dict | None, results: list[dict]) -> tuple[list[str], list[str]]:
    """(regressions, fixed): cases that passed (every run) last time and fail now, and the other way round.
    Cases missing from either run (--cases, --limit, new cases) are not compared."""
    if not previous:
        return [], []
    before = {cid: c["pass"] for cid, c in per_case(previous.get("results", [])).items()}
    now = per_case(results)
    regressions = [cid for cid, c in now.items() if before.get(cid) == 1 and c["pass"] < 1]
    fixed = [cid for cid, c in now.items() if cid in before and before[cid] < 1 and c["pass"] == 1]
    return regressions, fixed


def compare_reports(a: dict, b: dict) -> dict:
    """Per-case deltas from report a (before) to b (after): pass share, recall, median seconds and tool calls,
    for the cases both ran; and the totals of each."""
    ca, cb = per_case(a.get("results", [])), per_case(b.get("results", []))

    def delta(x, y):
        return None if x is None or y is None else round(y - x, 2)
    rows = []
    for cid in [c for c in ca if c in cb]:
        x, y = ca[cid], cb[cid]
        rows.append({"id": cid, "pass": (x["pass"], y["pass"]), "recall": (x["recall"], y["recall"]),
                     "seconds": (x["seconds"], y["seconds"]), "d_seconds": delta(x["seconds"], y["seconds"]),
                     "tool_calls": (x["tool_calls"], y["tool_calls"]),
                     "d_tool_calls": delta(x["tool_calls"], y["tool_calls"]),
                     "regressed": x["pass"] == 1 and y["pass"] < 1
                     or (x["recall"] is not None and y["recall"] is not None and y["recall"] < x["recall"]),
                     "fixed": x["pass"] < 1 and y["pass"] == 1})
    both = {r["id"] for r in rows}
    return {"rows": rows,
            "before": summarize([r for r in a.get("results", []) if r["id"] in both]),
            "after": summarize([r for r in b.get("results", []) if r["id"] in both]),
            "only_before": sorted(set(ca) - set(cb)), "only_after": sorted(set(cb) - set(ca))}


# ------------------------------------------------------------------ output

def _f(v, unit: str = "") -> str:
    if v is None:
        return "-"
    return f"{v:g}{unit}" if isinstance(v, (int, float)) else str(v)


def print_table(results: list[dict]) -> None:
    w = max((len(r["id"]) for r in results), default=10)
    for r in results:
        extra = f"  {r['seconds']}s" if r.get("seconds") is not None else ""
        if r.get("tool_calls") is not None:
            extra += f"  {r['tool_calls']} tools"
        if r.get("recall") is not None:
            extra += f"  recall {r['recall']:.0%}"
        why = "" if r["ok"] else "  " + "; ".join(r["problems"])
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']:<{w}}  {r['lang']}  {r['kind']:<10}{extra}{why}")
    passed = sum(r["ok"] for r in results)
    pct = 100 * passed / len(results) if results else 0
    print(f"\nscore: {passed}/{len(results)} ({pct:.0f}%)")


def print_summary(by_provider: dict[str, list[dict]]) -> None:
    """One line per provider, then each provider's five worst cases."""
    head = f"{'provider':<9} {'pass':>9} {'recall':>7} {'med s':>7} {'p90 s':>7} {'1st txt':>8} {'tools':>6} {'errors':>6}"
    print(head + "\n" + "-" * len(head))
    sums = {p: summarize(rs) for p, rs in by_provider.items()}
    for p, s in sums.items():
        rate = f"{s['passed']}/{s['runs']}"
        print(f"{p:<9} {rate:>9} {_f(s['mean_recall']):>7} {_f(s['median_s']):>7} {_f(s['p90_s']):>7} "
              f"{_f(s['median_first_text_s']):>8} {_f(s['median_tool_calls']):>6} {s['errors']:>6}")
    for p, s in sums.items():
        print(f"\nworst 5 on {p}:")
        for c in s["worst"]:
            why = "; ".join(c["problems"])[:110]
            print(f"  {c['id']:<34} pass {c['pass']:.0%}  recall {_f(c['recall'])}  {_f(c['seconds'], 's')}  "
                  f"{_f(c['tool_calls'])} tools  {why}")


def print_compare(cmp: dict, name_a: str, name_b: str) -> None:
    rows = cmp["rows"]
    w = max((len(r["id"]) for r in rows), default=10)
    print(f"{name_a}  ->  {name_b}\n")
    print(f"{'case':<{w}}  {'pass':>11}  {'recall':>11}  {'seconds':>17}  {'tools':>13}")
    for r in rows:
        mark = "  REGRESSED" if r["regressed"] else "  fixed" if r["fixed"] else ""
        pa, pb = r["pass"]
        ra, rb = r["recall"]
        sa, sb = r["seconds"]
        ta, tb = r["tool_calls"]
        print(f"{r['id']:<{w}}  {pa:>4.0%} -> {pb:<4.0%}  {_f(ra):>4} -> {_f(rb):<4}  "
              f"{_f(sa):>5} -> {_f(sb):<5} ({_f(r['d_seconds']):>5})  {_f(ta):>3} -> {_f(tb):<3} ({_f(r['d_tool_calls'])})"
              f"{mark}")
    a, b = cmp["before"], cmp["after"]
    print(f"\npass {a['passed']}/{a['runs']} -> {b['passed']}/{b['runs']}   mean recall {_f(a['mean_recall'])} -> "
          f"{_f(b['mean_recall'])}   median s {_f(a['median_s'])} -> {_f(b['median_s'])}   p90 s {_f(a['p90_s'])} -> "
          f"{_f(b['p90_s'])}   median tools {_f(a['median_tool_calls'])} -> {_f(b['median_tool_calls'])}")
    if cmp["only_before"] or cmp["only_after"]:
        print(f"not compared (in one report only): {', '.join(cmp['only_before'] + cmp['only_after'])}")


WARNING = """\
!! Live mode runs {n} question(s) through {who} on YOUR account(s).
!! Every question spends plan usage (roughly one normal question each). Model: {model}.
"""


def _confirm(args, n: int, who: str) -> bool:
    print(WARNING.format(n=n, who=who, model=args.model or "each CLI's default (Claude: sonnet)"))
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("not a terminal: pass --yes to confirm.")
        return False
    try:
        return input("Continue? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:          # some shells report a tty with nothing behind it
        print("\nno answer: pass --yes to confirm.")
        return False


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Hebrew on a Windows console
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Score Maple Helper answers against evals/answers.json.")
    ap.add_argument("--mode", choices=["quick", "live", "claude"], default=None,
                    help="quick (default), or live through the AI (claude = live on Claude, as before)")
    ap.add_argument("--provider", choices=[*PROVIDERS, ALL_SIGNED_IN],
                    help="live mode on this AI; all-signed-in: every CLI installed and signed in here")
    ap.add_argument("--model", help="live mode: the model passed to the CLI (default: each CLI's own; Claude sonnet)")
    ap.add_argument("--cases", help="comma-separated case ids, tags, kinds or id patterns (list-*)")
    ap.add_argument("--only", help="comma-separated case ids (same as --cases)")
    ap.add_argument("--case-file", type=Path, default=CASES)
    ap.add_argument("--kb", type=Path, default=KB)
    ap.add_argument("--limit", type=int, help="live mode: only the first N cases")
    ap.add_argument("--workers", type=int, default=1,
                    help="live mode: questions at once (default 1; more skews the timings, see the docstring)")
    ap.add_argument("--repeat", type=int, default=1, help="live mode: ask every case N times (answers vary)")
    ap.add_argument("--profile", help='live mode: one profile for every case, "level=35,job=Hunter,'
                                      'base_class=Bowman,name=X" (missing fields: Thief/Assassin 31), or "none"')
    ap.add_argument("--yes", action="store_true", help="live mode: skip the plan-usage confirmation")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"),
                    help="print the per-case deltas between two reports, then exit (1 on regressions)")
    args = ap.parse_args(argv)
    # each run's timings (sent, first sign of life, a hedge twin and who won) go to evals/reports/<...>.log: a slow
    # case can then be told apart from a slow server (shop-arrows-he took 61 s once, first text at the hedge time).
    # Live runs only: with --mode left out (quick) and for --compare, an empty log was made every run
    mode = args.mode or ("live" if args.provider else "quick")
    if mode != "quick" and not args.compare:
        REPORTS.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, filename=REPORTS / f"{time.strftime('%Y%m%d-%H%M%S')}.log",
                            encoding="utf-8", format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.compare:
        a, b = (json.loads(p.read_text(encoding="utf-8")) for p in args.compare)
        cmp = compare_reports(a, b)
        print_compare(cmp, args.compare[0].name, args.compare[1].name)
        return 1 if any(r["regressed"] for r in cmp["rows"]) else 0

    from maplehelper.kb import KnowledgeBase

    cases = load_cases(args.case_file)
    problems = validate_cases(cases)
    if problems:
        print("evals file is invalid:\n  " + "\n  ".join(problems))
        return 2
    try:
        cases = select_cases(cases, ",".join(s for s in (args.cases, args.only) if s))
        override = parse_profile(args.profile)
    except SystemExit as e:
        print(e)
        return 2
    if not (args.kb / "index.json").exists():
        print(f"no knowledge base at {args.kb}")
        return 2

    if mode == "quick":
        results = run_quick(cases, KnowledgeBase(args.kb))
        print_table(results)
        return 0 if all(r["ok"] for r in results) else 1

    if os.environ.get("CI") or os.environ.get("PYTEST_CURRENT_TEST"):
        print("live mode spends a real player's plan usage: refusing to run in CI or under pytest.")
        return 2
    provider = args.provider or "claude"
    names = signed_in_providers() if provider == ALL_SIGNED_IN else [provider]
    if not names:
        print("no AI CLI is installed and signed in on this PC.")
        return 2
    if args.limit:
        cases = cases[:args.limit]
    if not _confirm(args, len(cases) * max(1, args.repeat) * len(names), ", ".join(names)):
        return 2
    kb_dir = scratch_kb(args.kb)
    by_provider, exit_code = {}, 0
    try:
        kb = KnowledgeBase(kb_dir)
        kb.ensure_drop_table()          # once, before any workers start: each ask() only checks it
        for name in names:
            print(f"\n== {name} ==")
            results = run_live(cases, kb, name, args.model, args.workers, args.repeat, override,
                               on_result=lambda r: print(
                                   f"{'PASS' if r['ok'] else 'FAIL'}  {r['id']}  ({r['seconds']}s, "
                                   f"{_f(r.get('tool_calls'))} tools"
                                   + (f", recall {r['recall']:.0%}" if r.get("recall") is not None else "")
                                   + ")", flush=True))
            by_provider[name] = results
            print()
            print_table(results)
            path = save_report(results, args.model, provider=name, workers=args.workers, repeat=args.repeat,
                               profile=override, cases=args.cases or args.only)
            regressions, fixed = compare(previous_report(before=path, provider=name), results)
            print(f"report: {path.relative_to(ROOT)}")
            if fixed:
                print("fixed since the previous report: " + ", ".join(fixed))
            if regressions:
                print(f"REGRESSIONS on {name} (passed in its previous report, fail now): " + ", ".join(regressions))
                exit_code = 1
    finally:
        shutil.rmtree(kb_dir, ignore_errors=True)
    print()
    print_summary(by_provider)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
