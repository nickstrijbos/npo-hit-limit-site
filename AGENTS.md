# AGENTS.md - NPO Hit Limit Site

## What this is

Single-view Django app for the Torn game: hit-limit + ticket-payout stats pulled live via the Torn API. There is **no CSV mode** (removed). One page with three tabs — **Hit Limits**, **Chain** (NPO's outgoing hit log) and **Defenders** (enemy incoming hits, dogpile detection, push heatmap) — all rendered from a single POST payload. Core logic lives in `tracker/views.py`: `compute_stats()` (hit-limit seam), `normalize_attacks()` (live-row normalizer), `build_chain()` (per-direction log), `detect_group_attacks()` (dogpiles), `build_heatmap()` (enemy activity grid), and `index_view()` (request handling / per-tab dispatch via `_compute_tabs`).

## Commands

```bash
DEBUG=True python manage.py runserver   # local dev
python manage.py check                  # validate config
python manage.py test                   # tests are in tracker/tests.py — extend them when changing seam logic
python manage.py migrate                # creates db.sqlite3 (gitignored) for /admin/
```

## Gotchas

- `settings.py` was generated with Django 6.0.3 (`requirements.txt` pins `>=4.2.0`, so pip installs the latest). Dockerfile uses Python 3.13.
- `SECRET_KEY` is a hardcoded insecure dev key and is NOT read from an env var. Env vars are only: `DEBUG` (default `False`), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`.
- `db.sqlite3` is gitignored — a fresh clone has no DB until `migrate`.
- Project-root `static/` is NOT wired up (no `STATICFILES_DIRS`, no whitenoise) and the template hardcodes `/static/star.png` as favicon. Don't rely on `{% static %}` or static serving.
- Form field state is intentionally preserved across POSTs (`context.update` in views.py) — keep it.
- Processing errors are caught by a generic `except Exception` and rendered in-template as `context['error']`, not returned as 500s.

## Processing rules (views.py)

- Input is the normalized live-attacks DataFrame; "Valid" results: `Attacked`, `Hospitalized`, `Assist`, `Lost`.
- Optional exact-match `defender_faction` filter (applied inside `compute_stats`): matches the defender **faction ID** as a string.
- 24h/48h windows measured from the earliest attack timestamp (war start), not "now". Over limit = hits strictly > limit.
- Tickets (only when `show_tickets` checkbox on): `attacks*20 + assists*15 + losses*15` (no cap on losses).
- Respect column: sum of `respect_gain` over the whole (defender-filtered) window, including non-valid results.

## Live mode (the only mode)

- Browser fetches `/v2/faction/wars` + `/v2/faction/attacks` (both `filters=outgoing` and `filters=incoming`) + `/v2/faction` directly from `api.torn.com` (CORS is open). The API key never reaches Django — **only compact rows are POSTed**.
- POST fields: `attacks` (JSON array of compact rows), `war_start`/`war_end` (unix ts), `war_name`, `faction_names` (JSON map of faction ID → name, gathered from the wars response for display), `restored` (cache-restore flag), plus the shared limits/defender/tickets fields, the tab controls (`successful_only`, `def_successful_only`, `dogpile_window`, `dogpile_min`, `heatmap_bucket`, `auto_refresh`) and `manual_start`/`manual_end` (datetime-local values, round-tripped so the form keeps state).
- Compact row contract (server: `normalize_attacks()`): `[id, started, attacker_id, attacker_name, defender_id, defender_name, defender_faction, result, respect_gain?, direction?]` where `defender_faction` is the defender's **faction ID** (not name — filtering is by ID), `defender_id`/`defender_name` are the target, and `direction` is `'out'` (key owner attacked) or `'in'` (key owner was attacked), defaulting to `'out'`. Malformed rows are dropped with a warning count; duplicate ids keep the first; missing defender id/faction become `''`.
- Live mode is faction-agnostic: `filters=outgoing`/`incoming` scope to the key owner's faction — **no NPO filter** server-side.
- **Detect War** auto-fills the Defender Faction ID field with the war opponent's ID — the first `war.factions` entry that isn't one of the known NPO faction IDs (12645, 10610, 44758, 26885, 14052). No `/v2/faction` call; inter-NPO wars leave the field as-is. Detect War also builds the `faction_names` map from every known war in the response.
- The client-side fetch has three Torn API quirks baked in (verified live, see SPEC.md): `next` links omit `key=` (must re-attach), page boundaries are **inclusive** (dedupe by id is mandatory), and the cursor can stall at the tail (`next` repeats) — the JS breaks when a `next` URL repeats itself, with a 90-page cap as a backstop.

## Client-side state & refresh (localStorage)

- The merged payload + war window + form settings are cached under `torn_tracker_payload_v2` after every successful fetch. On page load the cache is **auto-re-submitted** (no Torn calls) unless the war ended over an hour ago; a `sessionStorage` flag (`torn_tracker_restored`) prevents restore loops. The API key stays in `torn_tracker_api_key`.
- **Refresh Live** and the **Auto-refresh (30s)** toggle do an *incremental* fetch: only attacks newer than `last_ts - 60s` are pulled, merged into the cache (dedupe by id), then re-POSTed — no backfill. A full backfill happens only when the window changed or the cache is empty.
- Payload is trimmed to stay under Django's POST body cap (oldest rows dropped first). Torn's own 30s service cache dedupes identical requests.
- Tab filter controls carry `data-auto-submit`: changing them natively submits the form with the existing hidden payload — a server re-render with zero Torn calls.
- **Start Over** clears the payload cache and reloads (empty form).
- Caching is entirely client-side — no server state, sessions or DB.

## Tab seams & rules (views.py)

- `build_chain(df, direction, successful_only, faction_names)` — newest-first per-direction log; `successful_only` keeps only `SUCCESS_RESULTS` (`Attacked`, `Hospitalized` — results that burn defender energy). `defender_faction_name` comes from the POSTed `faction_names` map.
- `detect_group_attacks(df, window_s, min_attackers)` — dogpile = ≥`min_attackers` **distinct** enemy attackers hitting the **same** NPO target within `window_s` seconds (first-to-last, inclusive). Only successful `in` hits count. Emits events (with `attack_ids`, so the enemy chain rows can be flagged `dogpile`).
- `build_heatmap(df, window_start, window_end, bucket_minutes)` — per enemy attacker, hit counts per bucket across the war window (counts **all** incoming attacks — activity signal). Granularity auto-raises (bucket size doubles) if attacker×bucket cells exceed `max_cells` (20k). Window falls back to data min/max when absent; `ts == window_end` clamps into the last bucket.
- Per-tab isolation: `_compute_tabs()` computes each tab in its own try/except, so one tab failing renders a per-tab error line, not a dead page. Hit-limits behavior (compute_stats) is unchanged.
- Respect colors: `_respect_color()` in views.py returns green scaled light→dark by magnitude (dark at +20) or red at ≤ 0; applied as inline `color` on the respect cells (hit-limit totals + both chain logs).
- All seams take DataFrames / plain values — pure, DB-free, unit-tested in `tracker/tests.py`.

## index.html client-side sorting

The sort JS addresses table cells by hardcoded index. Column order is: `0=#`, `1=name`, `2=hits_24h`, `3=hits_48h`, `4=attacks`, `5=assists`, `6=losses`, `7=respect`, `8=tickets`. The `ticketIndex` detection (respect = 7, tickets = 8) is part of this coupling — adding/removing a results column requires updating all of them together.

## Form state persistence

- `context.update` in views.py preserves limits/defender/tickets across POSTs — keep it.
- Live state round-trips through hidden inputs (`war_start_ts`/`war_end_ts`/`war_name` + manual datetimes) so **Refresh Live** can re-run the fetch with the same window.

## Docker / deploy

- Dockerfile: `python:3.13-slim`, gunicorn on `:8000` (3 workers), non-root `appuser`, **no migrate/collectstatic step** — the app works without a DB.
- `.github/workflows/docker-build.yml` builds and pushes `ghcr.io/nickstrijbos/npo-hit-limit-site:latest` on push to `main`.
