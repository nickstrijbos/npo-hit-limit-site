# AGENTS.md - NPO Hit Limit Site

## What this is

Single-view Django app for the Torn game: hit-limit + ticket-payout stats pulled live via the Torn API. There is **no CSV mode** (removed). Core logic lives in `tracker/views.py`: `compute_stats()` (the stats seam), `normalize_attacks()` (live-row normalizer), and `index_view()` (request handling / attack-payload parsing).

There are **no models** (`tracker/models.py` is empty) and the DB is never touched at runtime — processing is 100% in-memory pandas. `migrate` is only needed for `/admin/` (auth/sessions).

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

- Browser fetches `/v2/faction/wars` + `/v2/faction/attacks` + `/v2/faction` directly from `api.torn.com` (CORS is open). The API key never reaches Django — **only compact rows are POSTed**.
- POST fields: `attacks` (JSON array of compact rows), `war_start`/`war_end` (unix ts), `war_name`, plus the shared limits/defender/tickets fields and `manual_start`/`manual_end` (datetime-local values, round-tripped so the form keeps state).
- Compact row contract (server: `normalize_attacks()`): `[id, started, attacker_id, attacker_name, defender_faction, result, respect_gain?]` where `defender_faction` is the defender's **faction ID** (not name — filtering is by ID). Malformed rows are dropped with a warning count; duplicate ids keep the first; missing defender faction becomes `''`.
- Live mode is faction-agnostic: **no NPO filter** (`filters=outgoing` scopes to the key owner).
- **Detect War** auto-fills the Defender Faction ID field with the war opponent's ID — the first `war.factions` entry that isn't one of the known NPO faction IDs (12645, 10610, 44758, 26885, 14052). No `/v2/faction` call; inter-NPO wars leave the field as-is.
- The client-side fetch has three Torn API quirks baked in (verified live, see SPEC.md): `next` links omit `key=` (must re-attach), page boundaries are **inclusive** (dedupe by id is mandatory), and the cursor can stall at the tail (`next` repeats) — the JS breaks when a `next` URL repeats itself, with a 90-page cap as a backstop.

## index.html client-side sorting

The sort JS addresses table cells by hardcoded index. Column order is: `0=#`, `1=name`, `2=hits_24h`, `3=hits_48h`, `4=attacks`, `5=assists`, `6=losses`, `7=respect`, `8=tickets`. The `ticketIndex` detection (respect = 7, tickets = 8) is part of this coupling — adding/removing a results column requires updating all of them together.

## Form state persistence

- `context.update` in views.py preserves limits/defender/tickets across POSTs — keep it.
- Live state round-trips through hidden inputs (`war_start_ts`/`war_end_ts`/`war_name` + manual datetimes) so **Refresh Live** can re-run the fetch with the same window.

## Docker / deploy

- Dockerfile: `python:3.13-slim`, gunicorn on `:8000` (3 workers), non-root `appuser`, **no migrate/collectstatic step** — the app works without a DB.
- `.github/workflows/docker-build.yml` builds and pushes `ghcr.io/nickstrijbos/npo-hit-limit-site:latest` on push to `main`.
