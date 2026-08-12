# NPO Hit Limit Site

Django web app that audits Torn faction hit limits and ticket payouts. Two modes on one page, both processed fully in-memory:

- **CSV mode**: upload a YATA faction-attacks export, get per-member stats (unchanged).
- **Live mode**: paste your own Torn API key (browser-side only), auto-detect the current war, and pull attacks straight from the Torn API in near-real-time — no CSV export needed.

## Fresh install (new PC)

Requires Python 3.12+ (Django 6 installs from `requirements.txt`; the Docker image uses 3.13).

```bash
# 1. Clone
git clone git@github.com:nickstrijbos/npo-hit-limit-site.git
cd npo-hit-limit-site

# 2. Virtualenv + dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Create the SQLite DB — only needed for /admin/
#    (the hit-limit page itself never touches the database)
python manage.py migrate

# 4. Run the dev server
DEBUG=True python manage.py runserver
```

Open http://127.0.0.1:8000/

Optional: `python manage.py createsuperuser` for the Django admin at `/admin/`.

## Configuration (env vars)

| Var | Default | Notes |
|---|---|---|
| `DEBUG` | `False` | Set `True` for local dev |
| `ALLOWED_HOSTS` | `192.168.1.200,hitlimit.sterus.dev,localhost,127.0.0.1` | Comma-separated |
| `CSRF_TRUSTED_ORIGINS` | `https://hitlimit.sterus.dev` | Comma-separated |

Note: `SECRET_KEY` is hardcoded in `milcom_project/settings.py` (dev key) — there is no env override.

## Docker

```bash
docker build -t npo-hit-limit-site .
docker run -p 8000:8000 -e DEBUG=True -e ALLOWED_HOSTS=localhost,127.0.0.1 npo-hit-limit-site
```

Runs gunicorn on `:8000` as a non-root user. No volume is mounted and the image has no migrate step — nothing persists in the container, which is fine since the app doesn't use a DB.

## Getting the CSV

1. YATA → Faction → Attacks → **Create new attacks reports**
2. Set the Start Time to the war start and generate a Live report (YATA caps reports at 7 days)
3. Eye icon → **Export → Attacks**, upload the downloaded CSV

The parser needs the `timestamp_started` column and accepts comma or semicolon delimiters.

## Live mode (Torn API)

The page opens on the **Live API** tab (switch to CSV Upload for YATA exports):

1. Paste your Torn API key. A **Limited**-access key is enough — generate it at torn.com with the `faction → attacks` and `faction → wars` selections.
2. Click **Detect War** to auto-detect the current/upcoming ranked war (falls back to the latest raid, or set the window manually with the UTC datetime fields).
3. Click **Fetch & Process**. The browser pulls all faction attacks from war start to now, then submits compact rows to the server exactly like a CSV upload.

Notes:

- **Your key never leaves the browser.** It is stored in `localStorage` only, never sent to this site (Django only ever sees compact attack rows). This is stated on the page per Torn's ToS.
- Live mode is **faction-agnostic** — no NPO filter is applied (the `outgoing` filter already scopes to the key owner's faction). CSV mode keeps its NPO filter.
- If the detected war hasn't started yet, the page says so instead of showing an error; use the manual start field to track any window.
- A **↺ Refresh Live** button re-runs the fetch with the same window after processing, for pulling the latest attacks mid-war.
- Results show a per-member **Respect** total (sum of `respect_gain` over the fetched window). CSV mode reads a `respect`/`respect_gain` column if the export has one, else shows 0.
- The browser paginates `/v2/faction/attacks` (`limit=100`, `filters=outgoing`, asc), re-attaches the `key=` to every `next` link (Torn's next links omit it), dedupes by attack id (page boundaries are inclusive), and stops when the cursor stalls or ~90 pages are hit.
- Since the API can't see merc/guest hits, keep CSV mode for full coverage.

## Tests

```bash
python manage.py test
```

The first tests live in `tracker/tests.py` and cover the shared stats seam (`compute_stats`) and the live row normalizer (`normalize_attacks`) — the two functions both modes funnel through.

## Deployment

Pushing to `main` triggers GitHub Actions (`.github/workflows/docker-build.yml`) to build and push `ghcr.io/nickstrijbos/npo-hit-limit-site:latest`.
