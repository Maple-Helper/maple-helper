# Live content: changes with no app release

Code needs a release. Content doesn't. These files reach every open copy of the app within about 20 minutes of
being merged to `main`, with no new version (`maplehelper/live.py`):

| File (under `assets/`) | What it is |
|---|---|
| `live/config.json` | The owner's announcements, feature switches, lowest supported version, UI text fixes |
| `official/facts.json` | Official game facts: they override the knowledge base, and the AI is told so |
| `ai/system.md`, `ai/reply_rules.md`, `ai/rules.json` | The AI's instructions (system prompt, reply rules, the shorter rules) |
| `items/he.json`, `skills/he.json`, `news/he.json`, `cashshop/he.json`, `pet_skills/he.json`, `quest_tasks/he.json`, `skill_changes/he.json` | The curated Hebrew of the KB's texts |
| `guides/he/*.json`, `guides/en/*.json` | The guides (and the glossary inside them) |

The knowledge base itself (game data, Nexon news, the nightly's translations) already updates with no release:
see [RELEASING.md](RELEASING.md).

## How it works

`assets/live.json` lists every live file with its checksum. Every 15 minutes, with the KB check, the app reads that
list from `raw.githubusercontent.com/Maple-Helper/maple-helper/main/assets/live.json`, and downloads each file that
differs from the copy it shipped with into `%APPDATA%\MapleHelper\live\assets`, where it wins over the shipped one.

- Only a file the app already ships can be replaced, never a new one, and nothing downloaded is ever run.
- A file must match its checksum and parse; otherwise it is skipped until the next check.
- Every reader falls back to the shipped copy when a live one doesn't fit (the AI's instructions are checked for
  their markers and placeholders; a text fix must keep the text's `{placeholders}`).
- A new app version drops everything downloaded for the old one (its own files are at least as new).

## Changing one

1. Edit the file (for the config, `tools/live_edit.py` does it for you, see below).
2. Run `python tools/live_manifest.py` (a test fails while `assets/live.json` is out of date).
3. Open a PR, wait for green CI, merge. Live within about 20 minutes (GitHub's cache up to 5 minutes, the app's check
   every 15).

To see a change before merging it, push the branch and start a test copy with
`MAPLEHELPER_LIVE_BRANCH=<branch name>`: it reads the list from that branch instead of `main`.

### Announcements

```
python tools/live_edit.py announce --id launch-week --he "..." --en "..." \
    [--title-he "..." --title-en "..."] [--link https://... --link-he "..." --link-en "..."] \
    [--from 2026-10-25] [--until "2026-10-30 18:00"] [--min-app 0.19.0] [--max-app 0.20.0] \
    [--lang he|en] [--warning] [--toast]
python tools/live_edit.py remove --id launch-week
```

The chat shows them at its top, one at a time in the config's order, until the player closes one (each `id` once:
a new message needs a new `id`). `--toast` also pops a notification once, even with the chat closed. Dates are the
player's local time; a date alone covers that whole day. `--min-app` / `--max-app` limit an announcement to some
versions; `--lang` to players of one language (otherwise a missing language falls back to the other one).
Telemetry counts `announcement_seen`, `announcement_opened`, `announcement_dismissed`, `announcement_toasted`.

### Switches

```
python tools/live_edit.py flags                  # lists them
python tools/live_edit.py flag mesowatch off
```

| Switch | Off means |
|---|---|
| `mesowatch` | No MesoWatch prices or checks (cards show no sales line) |
| `osms_shop` | The Shop page has nothing; no OSMS checks |
| `server_status` | No server status checks |
| `game_toolbar` | The bar over the game is hidden |
| `quickslot` | The grind tracker stops reading the quick slots (potions) |
| `provider_claude`, `provider_codex`, `provider_gemini`, `provider_grok` | That AI answers "turned off for now, pick another AI" |
| `kb_update` | No KB updates (holds a bad KB back) |
| `app_update` | No app updates (holds a bad release back) |
| `telemetry` | No usage stats or presence pings are sent |

### Lowest supported version

```
python tools/live_edit.py min-app 0.19.0          # "" = every version
```

Older copies show a warning at the top of the chat ("no longer supported, update") with a download link, and look
for an update at once.

### UI text fixes

```
python tools/live_edit.py string err_generic --he "..." [--en "..."]
python tools/live_edit.py unstring err_generic
```

Any text in `maplehelper/i18n.py`, by its key. The fix must keep the original's `{placeholders}`, or it is ignored.
Fix it in `i18n.py` too before the next release, and remove the override after it.

## Format changes (developers)

Each kind of live file has a contract number (`live.CONTRACTS`). When a release reads a kind differently (a new
required field, a renamed key, what the app parses from the AI's answer, the META block), bump that kind's number in
the same PR and run `tools/live_manifest.py`: the list then names the new number, and older apps keep their shipped
copy of that kind instead of reading a file they don't understand. Changes that only add or reword content need no
bump.
