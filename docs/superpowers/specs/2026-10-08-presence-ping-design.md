# Presence ping: how many players, and how many online now

## Goal

The owners want to see, in PostHog, **how many people use Maple Helper** and **how many are running it right
now**, counting every install, not only players who opted in to the usage stats.

## What exists

`maplehelper/telemetry.py` sends anonymous events to PostHog (US cloud, project 643525) under a random
`install_id`, but only when the player turns on Settings > Privacy & system > "Share anonymous usage stats"
(`settings["telemetry"]`, default `False`). Its counts cover only the opted-in players, and it has no signal
for "still running" (only `app_started`).

## Decision

A separate **presence ping**, on for everyone by default, that **carries no persistent identifier**. The
opt-in usage stats stay exactly as they are (default off, same events).

Rejected: flipping `telemetry` to default-on. The stored `False` can't tell "chose off" from "never looked",
so it would enroll players who said no, and it would send the detailed events without consent.

## The ping

One PostHog event, `app_running`, posted by a daemon thread:

- at startup (after onboarding, next to `telemetry.init`), then **every 10 minutes** while the app runs
- `distinct_id`: a random uuid made **per process run**, never written to disk. Two launches can't be linked
- properties, nothing else:
  - `app_version`, `$os`, `frozen` (False = a source run, filtered out of the dashboard)
  - `first_ping`: true the first time this install ever sends a ping (gives total users)
  - `first_today`: true on the first ping of the UTC day (gives daily active users)
  - `first_this_month`: true on the first ping of the UTC month (gives monthly active users)
  - `$process_person_profile: false`, `$geoip_disable: true`
- the flags are stored in settings (`presence_sent`: `{"ever": bool, "day": "YYYY-MM-DD", "month": "YYYY-MM"}`)
  **only after a successful post**, so a ping lost offline carries its flags into the next one
- a failed post is logged at info level and dropped, like telemetry. It never blocks or raises

An install that upgrades from 0.13 or earlier sends `first_ping` on its first run of the new version. So
"total users" counts every install that has run this version or later, not historical installs.

### Turning it off

- new setting `presence` (default `True`), shown as a switch in Settings > Privacy & system next to the
  usage-stats switch: "Count that Maple Helper is running (anonymous)", with a hint stating exactly what is
  sent (he + en in `i18n.py`)
- `MAPLEHELPER_NO_TELEMETRY` also stops it, and an empty PostHog key stops it
- switching it off stops the timer at once (`on_settings_changed`)

## Code shape

- **`maplehelper/presence.py`** (new, small): `start(settings, version)`, `set_enabled(on)`, `stop()`, and a
  pure `payload(settings, version, run_id, now) -> (event, flags_to_save)` for tests
- **`telemetry.py`**: `_post(batch)` is reused (same host and key, the key is write-only). No behavior change
- **`store.py`**: defaults `"presence": True`, `"presence_sent": {}`. `report.py` adds `presence` to the
  settings it logs
- **`app.py`**: `presence.start(...)` after `telemetry.init`. `presence.set_enabled(...)` in
  `on_settings_changed`. `presence.stop()` in `shutdown`
- **`ui/dialogs.py`**: the switch and its save
- **README "Data and privacy"** and **What's New** notes: one short paragraph on what the ping sends, and how
  to turn it off

## PostHog side (owner setup, once)

- Project settings: turn on **Discard client IP data** (a POST always carries the IP; this keeps it out of
  stored events)
- Dashboard "Players", filtered to `frozen = true`:
  - **Online now**: `select count(distinct distinct_id) from events where event = 'app_running' and
    timestamp > now() - interval 15 minute and properties.frozen` (one missed ping is tolerated)
  - **Total users**: count of `app_running` with `first_ping = true`, all time (and per week as a trend)
  - **Daily / monthly active**: trend of `app_running` where `first_today` / `first_this_month`
  - **Online over the day**: trend of unique `distinct_id` per hour
  - **Versions and OS**: `first_today` events broken down by `app_version` / `$os`

## Volume

One ping per 10 minutes is 6 events per player-hour. PostHog's free tier is 1M events/month, about 166k
player-hours. Example: 1,000 players at 3 h/day is about 540k events/month. If it gets close to the limit,
raise the interval (the online window grows with it).

## Testing

`tests/test_presence.py`, network mocked like `test_telemetry.py`:

- default on. `presence=False`, `MAPLEHELPER_NO_TELEMETRY` or an empty key send nothing
- the payload carries no `install_id`, no setting other than the listed ones, and a run id that differs
  across two starts
- flags: first ever, first today, first this month, then all false on the next ping the same day. A new
  UTC day gives `first_today` only
- a failed post leaves `presence_sent` unchanged, so the next ping repeats the flags
- the opt-in telemetry is untouched: still off by default, still sends nothing when off

## Out of scope

A custom admin page (PostHog is the dashboard), counting installs from before this version, and any change
to the opt-in usage stats.
