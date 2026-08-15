# Live Faction Attacks Tracker (Torn API v2) — Spec

## Problem Statement

When a war starts, the hit-limit tracker currently depends on manual YATA CSV exports. YATA reports can't be generated quickly mid-war, so the CSV import "isn't working" at the exact moment it's needed most: the push times right after war start. Staff need near-real-time per-member stats — hit limits, ticket payouts, and respect gains — without waiting for CSV reports.

Separately, the site is used by multiple factions, each with its own staff and its own API keys, so a single server-side key baked into the app cannot serve everyone.

## Solution

A faction staff member pastes their own Torn API key (a Limited-access key is sufficient). The key stays in the user's browser (`localStorage`) and is never sent to, or stored by, the Django server — the Torn API allows browser calls directly (CORS is wide open).

The browser:

1. Calls `/v2/faction/wars` to auto-detect the current or upcoming ranked war and its declared start/end times (with a manual datetime fallback).
2. Calls `/v2/faction` to learn the key owner's faction ID, then auto-fills the Defender Faction ID field with the war opponent's ID.
3. Pulls all faction attacks from war start to now via `/v2/faction/attacks` in **both directions** (`filters=outgoing` + `filters=incoming`, forward pagination via `_metadata.links.next`), tagging each row with its direction.
4. Posts compact attack rows to the page as a normal form submit.

Django normalizes those rows into a pandas pipeline producing three tab views: hit-limit/ticket stats, the faction chain log (outgoing, newest first), and the enemy/defender view (incoming chain + dogpile detection + push heatmap). **CSV mode has been removed** — live is the only mode.

## User Stories

1. As a faction staff member, I want to get stats during a war without exporting a CSV.
2. As a faction staff member, I want to paste my Torn API key once and have it remembered in my browser, so that I don't retype it on every refresh.
3. As a faction staff member, I want my API key to never leave my browser, so that I stay comfortable sharing it (no server storage, no key in server logs).
4. As a faction staff member, I want the page to automatically detect the current ranked war from my key, so that I don't have to look up the war start time.
5. As a faction staff member, I want the detected war to show its name, opponent, and start/end times, so that I can confirm the right war is being tracked.
6. As a faction staff member, I want to manually override the war start/end times, so that I can track any window even when no war is detected.
7. As a faction staff member, I want to see a progress indicator while attacks are being fetched, so that long backfills don't look frozen.
8. As a faction staff member, I want friendly error messages for bad keys, wrong access levels, and rate limits, so that I know how to fix the problem.
9. As a faction staff member, I want a clear "war hasn't started yet" message when an upcoming war has zero attacks, so that an empty fetch isn't shown as an error.
10. As a faction staff member, I want per-member hit counts in the 24h and 48h windows since war start, so that I can audit hit-limit compliance.
11. As a faction staff member, I want members over their 24h/48h limits flagged in red, so that I can see violations at a glance.
12. As a faction staff member, I want per-member attack/assist/loss breakdowns, so that I can compute ticket payouts.
13. As a faction staff member, I want the 2/3-rule ticket payout calculation (attacks × 20 + assists × 15 + paid losses × 15), so that I can pay members accurately.
14. As a faction staff member, I want unpaid losses rendered in red with a hover tooltip, so that I can see where the 2/3 rule reduced payout.
15. As a faction staff member, I want a per-member total respect-gain column, so that I can see who is contributing most during the war.
16. As a faction staff member, I want Detect War to fill in the defender faction ID automatically, so that the results only count hits on the war opponent.
17. As a faction staff member from any faction, I want the live mode to work with my own faction's key, so that the tool isn't NPO-specific.
18. As a faction staff member, I want to filter by defender faction ID, so that I can exclude hits on non-war targets.
19. As a faction staff member, I want the form fields (limits, defender filter, tickets toggle) to persist after processing, so that I can tweak and re-run easily.
20. As a faction staff member, I want a Refresh action on live results, so that I can pull the latest attacks without re-entering data.
21. As a faction staff member, I want the results metadata to show how many attacks were fetched and the window covered, so that I can trust the numbers.
22. As a faction staff member, I want sortable columns (including the new respect column), so that I can rank members by any metric.
23. As a faction leader, I want the results to cover the exact war window (war start → war end/now), so that pre-war and post-war hits don't pollute the stats.

## Implementation Decisions

### Architecture: key stays in the browser

Verified live against the Torn API (CORS: `access-control-allow-origin: *`, `access-control-allow-headers: *`). The browser fetches directly from `api.torn.com` using the `key=` query parameter — **`Authorization: Bearer` is rejected by the API** (verified with a live Limited key). Django never sees the key: it receives only the compact attack rows. This satisfies Torn's ToS ("stored locally / not shared", temporary data, Limited access).

### API contract (verified with a live key)

- **Wars**: `GET /v2/faction/wars?key=...` → `{pacts: [], wars: {ranked: {war_id, start, end, target, winner, factions: [{id, name, ...}, ...]}, raids: [...], territory: [...]}}`. `ranked` is a single object (current or upcoming war; `end` is `null` while upcoming/active); `raids`/`territory` are arrays. Example from live data: upcoming ranked war vs "Helvete X", `start` 1786629600, `end: null`.
- **Attacks**: `GET /v2/faction/attacks?key=...&limit=100&filters=outgoing&sort=asc&from=<war_start>&to=<war_end|omitted>` → `attacks[]` + `_metadata.links.next`. Forward pagination: follow `links.next` until `null` (verified end-to-end: 3 pages, 247 attacks, correct boundaries). `to` bounds the far end and is preserved in `next` links. Boundaries are non-inclusive — no duplicate attacks across pages. The same fetch is run a second time with `filters=incoming` (attacks against the key owner's faction); each direction's rows are tagged `'out'`/`'in'` client-side. `filters=incoming` was not yet live-verified at implementation time — see Verification below.
- **Opponent detection**: no extra API call — since all users are NPO, Detect War identifies the war opponent as the first `war.factions` entry whose ID is **not** one of the known NPO faction IDs (12645, 10610, 44758, 26885, 14052), and auto-fills the Defender Faction ID field with it. Inter-NPO wars (both factions NPO) leave the field as-is. Clearing the field shows all hits including outside hits.
- **Retention**: the attacks log reaches back ~1 year (verified: oldest attack ~360 days old). `from=0` is buggy (`next: null`) — irrelevant, we always send a real war-start timestamp.
- **Rate limits**: 100 requests/min per key owner; the 30s service cache dedupes identical requests (repeated refreshes with a fixed window hit cache and don't consume quota).
- **Key access**: Limited-access keys cover both `faction/wars` and `faction/attacks` — the minimum a staff member needs to bring.

### War window detection

War start = `wars.ranked.start` (official declared start — matches the existing CSV workflow's instruction to "select the exact Start Time of the war"). War end = `wars.ranked.end` if set, else now. If `wars.ranked` is absent, fall back to the most recent `raids` entry, then to manual datetime fields (always shown as a fallback). Edge case: upcoming war (`start` > now) yields zero attacks — render a friendly "war starts at <time>" message rather than an error.

### Compact row contract (from the live-fetch prototype)

The browser trims each attack to a fixed-width row before posting, keeping the POST well under Django's default 2.5MB body cap even for ~9k attacks:

```
[id, started, attacker_id, attacker_name, defender_id, defender_name, defender_faction, result, respect_gain?, direction?]
```

`defender_faction` = defender's **faction ID** (or empty; matches the Defender Faction ID form field, which Detect War auto-fills with the war opponent's ID), `defender_id`/`defender_name` = the target player, `result` = Torn result string, `direction` = `'out'`/`'in'` (defaults `'out'`). Dedupe by `id` client-side. Django validates types and drops malformed rows with a warning count.

### Tab views (one POST feeds all three)

- **Hit Limits** (unchanged behavior): `compute_stats()` per-member limits/tickets/respect, with the existing sortable table.
- **Chain**: `build_chain(df, 'out', successful_only, faction_names)` — newest-first outgoing log with target + defender faction name (from the `faction_names` map gathered via Detect War) and color-coded respect (+green / −red / 0 gray).
- **Defenders**: `build_chain(df, 'in', ...)` (enemy chain, rows flagged `dogpile`), `detect_group_attacks(df, window_s, min_attackers)` (dogpile events: ≥N distinct enemy attackers on the same NPO target within a window, successful hits only), and `build_heatmap(df, war_start, war_end, bucket_minutes)` (per-enemy-member activity grid across the war window — counts **all** incoming attacks as an activity signal, auto-coarsens granularity past 20k cells). All three are computed independently in `_compute_tabs()` — one tab failing renders a per-tab error line.

### Client-side state & refresh

The merged payload + window + settings are cached in `localStorage` (`torn_tracker_payload_v2`) after every successful fetch and **auto-re-submitted on page load** (zero Torn calls; `sessionStorage` guard prevents restore loops). Refresh / Auto-refresh (30s) run an *incremental* fetch (`from = last_ts - 60s`) merged into the cache. Payloads are trimmed to the POST body cap (oldest rows dropped first). Tab filter controls `data-auto-submit` re-POST the existing payload for a server-side re-render without touching the API.

### Single processing seam

All stats computation lives in pure functions over the normalized DataFrame: `compute_stats()` (hit limits/tickets — unchanged), `build_chain()`, `detect_group_attacks()`, `build_heatmap()`. Live mode normalizes the compact rows into the DataFrame via `normalize_attacks()`. These are the **seams** at which the feature is tested — every other moving part (row normalization, browser fetch) is thin and unchanged.

### Live mode is faction-agnostic

No hardcoded "NPO" attacker filter — `filters=outgoing` already scopes results to the key owner's faction, so any faction's staff can use the tool. The defender-faction-ID exact-match filter applies to all rows.

### Respect column

Per-member `respect` total = sum of `respect_gain` over the fetched window. Windowed 24h/48h respect is deferred.

### Client-side fetch behavior

Sequential awaited pagination with ~400ms pacing between pages, capped at ~90 pages with a clear "window too large, narrow the range" error. Torn error payloads (`{"error": {code, error}}`) mapped to friendly messages: code 2 → bad key, code 5 → rate limited (retry once after a pause), code 16 → key access level too low, code 1 → key missing. Runs twice per fetch (outgoing then incoming). Incremental refreshes start at `last_ts - 60s` and merge into the cached payload.

### Sorting and column indices

The results table gains a Respect column between Losses and Tickets. The existing client-side sort JS addresses cells by hardcoded index — Tickets shifts from index 7 to 8, and the sort switch needs a respect case. Both must be updated together.

### No new dependencies

The fetch is client-side; `requests` is not needed. No DB/models changes (processing stays 100% in-memory). The API key is never persisted server-side — `localStorage` only.

## Testing Decisions

- **What makes a good test here**: only external behavior of the seams — given a DataFrame of attacks (or compact rows), the computed per-member results, chain rows, dogpile events and heatmap buckets are correct. No tests for Django request handling, the template, or network behavior (the browser fetch is not unit-testable in this codebase's current shape).
- **Modules to test**: the extracted stats function (24h/48h window boundaries, over-limit flags, attack/assist/loss breakdown, 2/3-rule paid-losses cap, respect totals, defender-faction filter, empty-input errors), the row-normalization function (malformed rows dropped, dedupe by id, missing defender fields, direction validation), and the new seams (chain ordering/direction/success filters/faction names, dogpile window boundaries and distinct-attacker minimum, heatmap bucket boundaries/auto-coarsening).
- **Prior art**: none — the project originally had zero tests; these are the first, using Django's built-in `TestCase`/`SimpleTestCase` with synthetic pandas DataFrames, run via `python manage.py test`.

## Out of Scope

- Server-side key storage or a shared multi-faction dashboard (deliberately avoided; keys stay browser-side).
- Coverage of merc/guest or external hits — an inherent Torn API limitation (the reason CSV mode was dropped: it didn't change this, and live is the workflow that matters).
- Push notifications / Discord integration.

## Verification

- `filters=outgoing` was live-verified previously (pagination, boundaries, `next`-link quirks).
- Pending live verification (needs an NPO key): `filters=incoming` returns attacks against the key owner's faction; `respect_gain` sign on lost attacks; the incremental `from = last_ts - 60s` trickle against real page boundaries; heatmap bucket alignment with declared war start/end.

## Further Notes

- Verified with a live Limited-access key during discovery: wars shape, attacks pagination, `to`/`from` semantics, ~1-year retention, `key=`-param-only auth, CORS support.
- Torn ToS note for the UI: key is stored in the user's browser only, not shared, temporary data, Limited access — the site should state this inline next to the key input.
- Key access instructions for staff: generate a key with `faction → attacks` and `faction → wars` selections (Limited access level).
- `localStorage` note: keys persist per browser; add a way to clear/replace the stored key (the input is prefillable and editable).
