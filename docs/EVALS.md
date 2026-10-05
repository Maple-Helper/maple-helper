# Answer evals

`evals/answers.json` holds ~90 questions written the way players ask them (mostly Hebrew, with
transliterations like "בלו סנייל", "מאנו"), each with checks whose expected facts come from the real
knowledge base in `data/kb`. `tools/eval_answers.py` scores answers against them.

## Two modes

| | quick (default) | live (`--provider ...`, or the old `--mode claude`) |
|---|---|---|
| What it tests | `maplehelper/quick.py` (instant answers) | the real `Brain`: prompt, KB context, the AI CLI |
| Cases | those with `"instant"` set | all (or `--cases`, `--limit N`) |
| Cost | free, no network, a few seconds | **spends plan usage on your account(s)**, ~10-60 s per case |
| Where it runs | by hand and in pytest (`tests/test_answer_evals.py`, skipped without `data/kb`) | by hand only; refuses under CI or pytest |
| Fails when | any case fails (exit 1) | a case that passed in that provider's previous report fails now (exit 1) |

```powershell
$env:PYTHONPATH = "C:\path\to\MapleHelper"
.venv\Scripts\python.exe tools\eval_answers.py                                    # quick
.venv\Scripts\python.exe tools\eval_answers.py --provider claude                  # asks before spending usage
.venv\Scripts\python.exe tools\eval_answers.py --provider codex --cases hard --yes
.venv\Scripts\python.exe tools\eval_answers.py --provider all-signed-in --yes     # every CLI signed in here
.venv\Scripts\python.exe tools\eval_answers.py --compare evals\reports\A.json evals\reports\B.json
```

Live-mode options:

- `--provider claude|codex|gemini|grok|all-signed-in`: the AI. `all-signed-in` asks each provider's own account
  check (`account()`, read-only) and runs every CLI that is installed and signed in, one after the other.
- `--model M`: passed to the CLI as the app does (default: the CLI's own; Claude: sonnet).
- `--cases a,b`: case ids, tags (`hard`, `list`, `quest`, `shop`, ...), kinds or id patterns (`list-*`). A word
  that matches nothing stops the run before it spends anything. `--only` is the same.
- `--workers N` (default 1): N questions at once, one CLI process each. Faster, but the processes compete for CPU
  and the provider's rate limits, so **seconds from a `--workers 4` run are not comparable** with a
  `--workers 1` baseline. Use 1 for any timing you compare.
- `--repeat N`: every case N times (answers vary); the report folds the runs per case (pass share, mean recall,
  median times).
- `--profile "level=35,job=Hunter,base_class=Bowman,name=X"`: one profile for every case (missing fields come
  from the default), or `none` for no profile. Without it each case uses its own `"profile"` over the default,
  **Thief / Assassin level 31**.

Each live answer records `seconds` (the whole answer), `first_text_s` (until the first visible text streamed:
Codex doesn't stream, so there it is the whole answer), `tool_calls` and `turns` as the CLI reports them
(`null` where it can't tell: Claude Code and Grok give both, Codex its commands, Gemini its tool steps), `recall`
for list cases, cost and model. The run works on a copy of the KB (without pictures), so the app's grep tables
are never written into `data/kb`.

Reports go to `evals/reports/<timestamp>-<provider>.json` (gitignored: answer texts, timings, cost) with a
`summary`, and are compared with the newest earlier report **of the same provider** (regressions and fixes).
At the end a table shows per provider: pass rate, mean recall, median and p90 seconds, median time to first
text, median tool calls, errors, and the 5 worst cases.

`--compare BEFORE AFTER` (free) prints per case the pass share, recall, median seconds and tool calls of both
reports with their deltas, then the totals; exit 1 when a case regressed (passed before and fails now, or its
recall dropped). Use it for before/after a change: run the same `--cases` with the same `--workers` on both sides.

## When to run live mode

- Before a release that changes `SYSTEM_PROMPT`, `REPLY_RULES`, `build_prompt`, the model, the KB tables, a
  provider backend or anything else that shapes answers. Run it once on the current release first if there is no
  recent report, so the comparison has a baseline.
- After a knowledge-base change that renames or restructures pages the AI reads.
- Not for quick.py changes: quick mode covers those for free.

AI answers vary between runs. A single failure on a fuzzy case (training spots, guides) is worth reading
in the report before acting on it; a run of failures in one kind is a real regression. `--repeat 3` on the
cases in doubt settles it.

## Adding a case

```json
{"id": "where-lupin-he", "question": "באיזו מפה יש לופין", "lang": "he", "kind": "where",
 "checks": {"must_mention": ["Monkey Swamp I"], "entities_include": ["monster/35"], "instant": true}}
```

- `id`: lowercase-with-dashes, unique. `lang`: `he` or `en`. `kind`: stats, drops, who_drops, where, npc,
  quest, job, training, guide, judgement, screenshot, list, equip, shop, craft, compare, skills.
- `tags` (optional): lowercase words for `--cases` (the recall / speed set is tagged `hard`).
- `profile` (optional): `level`, `job`, `base_class`, `name` for a level- or job-based question; missing fields
  come from the default (Thief / Assassin 31).
- `why`: how the expected facts were worked out (required for list checks), so a later KB change can be
  re-checked the same way.
- Take every expected fact from `data/kb` (`index.json` props, the page under `pages/`), never from memory or
  from what the app currently answers. Prefer distinctive values (HP 7420, not Level 2).
- Checks (matching ignores case and thousands separators: "7,420" = "7420"; the answer text plus the names
  on its cards are searched):
  - `must_mention`: every string must appear.
  - `must_mention_any`: at least one must appear (for open questions such as training spots).
  - `must_not_mention`: none may appear.
  - `entities_include`: KB keys that must be among the answer's cards (entities or drop groups).
  - `must_list`: names that must ALL appear (a list answer); the report gives `recall` = the share found, and
    the case passes only at 1.0. Work the list out from the pages yourself (not from the app's tables or its
    answers), in game only (`maplehelper/availability.py`), and say how in `why`.
  - `must_not_list`: names that must not appear (out-of-game quests, shops in Orbis / El Nath).
  - `instant`: `true` = quick.py must answer it, correctly; `false` = quick.py must leave it to Claude
    (judgement, "should I", the screenshot, two questions in one). Leave it out when the case is for
    live mode only (NPCs, quests, guides, lists).
- Run quick mode. If a new case fails because quick.py is wrong, fix quick.py with a regression test in
  `tests/test_quick.py`; don't weaken the case to match the bug.
