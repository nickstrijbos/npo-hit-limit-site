import json
import math
from datetime import datetime, timezone

from django.shortcuts import render
import pandas as pd

VALID_RESULTS = ('Attacked', 'Hospitalized', 'Assist', 'Lost')
# Hits that actually burn the defender's energy — the only results that
# matter for group-attack / push detection.
SUCCESS_RESULTS = ('Attacked', 'Hospitalized')
DIRECTIONS = ('out', 'in')


def compute_stats(df, limit_24h, limit_48h, defender_faction, show_tickets):
    """Pure per-member stats over a normalized attacks DataFrame.

    Input DataFrame must have timestamp_started, attacker_id, attacker_name,
    defender_faction, result and (optionally) respect_gain/'respect' columns.
    """
    if 'respect_gain' not in df.columns:
        if 'respect' in df.columns:
            df = df.rename(columns={'respect': 'respect_gain'})
        else:
            df = df.assign(respect_gain=0.0)
    df['respect_gain'] = pd.to_numeric(df['respect_gain'], errors='coerce').fillna(0.0)

    if defender_faction:
        df = df[df['defender_faction'].astype(str) == defender_faction]

    if df.empty:
        raise ValueError(f"No attacks found against '{defender_faction}'.")

    df = df.sort_values(by='timestamp_started')
    war_start_time = df['timestamp_started'].min()

    df_valid = df[df['result'].isin(VALID_RESULTS)].copy()
    if df_valid.empty:
        raise ValueError(f"No valid attacks found against '{defender_faction}'.")

    df_valid['hours_since_start'] = (df_valid['timestamp_started'] - war_start_time) / 3600

    # Respect totals span the whole (defender-filtered) window, not just valid results
    respect_totals = df.groupby('attacker_id')['respect_gain'].sum()

    results = []
    grouped = df_valid.groupby(['attacker_id', 'attacker_name'])

    for (attacker_id, attacker_name), group in grouped:
        # --- LIMIT CALCULATOR (Attacked + Hospitalized + Assist) ---
        hits_24h = len(group[(group['result'].isin(['Attacked', 'Hospitalized', 'Assist'])) & (group['hours_since_start'] <= 24)])
        hits_48h = len(group[(group['result'].isin(['Attacked', 'Hospitalized', 'Assist'])) & (group['hours_since_start'] <= 48)])

        # --- TICKET CALCULATOR (Breakdown) ---
        attacks = len(group[group['result'].isin(['Attacked', 'Hospitalized'])])
        assists = len(group[group['result'] == 'Assist'])
        losses = len(group[group['result'] == 'Lost'])

        # Calculate tickets only if feature flag is enabled
        if show_tickets:
            tickets = (attacks * 20) + (assists * 15) + (losses * 15)
        else:
            tickets = 0

        results.append({
            'id': attacker_id,
            'name': attacker_name,
            'hits_24h': hits_24h,
            'over_24h': hits_24h > limit_24h,
            'hits_48h': hits_48h,
            'over_48h': hits_48h > limit_48h,
            'attacks': attacks,
            'assists': assists,
            'losses': losses,
            'respect': float(respect_totals.get(attacker_id, 0.0)),
            'respect_color': _respect_color(respect_totals.get(attacker_id, 0.0)),
            'tickets': tickets
        })

    # Sort by Tickets descending
    return sorted(results, key=lambda x: x['tickets'], reverse=True)


def normalize_attacks(rows):
    """Normalize compact live rows into the shared DataFrame shape.

    Each row: [id, started, attacker_id, attacker_name, defender_id,
    defender_name, defender_faction, result, respect_gain?, direction?]
    (respect_gain and direction optional; direction defaults to 'out').

    Returns (df, warnings): malformed rows are dropped and counted; duplicate
    ids keep the first occurrence.
    """
    cleaned = []
    seen_ids = set()
    warnings = 0

    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            warnings += 1
            continue
        try:
            attack_id = int(row[0])
            started = float(row[1])
            attacker_id = int(float(row[2]))
        except (TypeError, ValueError):
            warnings += 1
            continue
        if attack_id in seen_ids:
            continue
        seen_ids.add(attack_id)

        try:
            respect = float(row[8]) if len(row) > 8 and row[8] not in (None, '') else 0.0
        except (TypeError, ValueError):
            respect = 0.0

        direction = str(row[9]).strip() if len(row) > 9 and row[9] not in (None, '') else 'out'
        if direction not in DIRECTIONS:
            warnings += 1
            continue

        defender_id = str(row[4]) if row[4] not in (None, '') else ''
        cleaned.append({
            'id': attack_id,
            'timestamp_started': started,
            'attacker_id': attacker_id,
            'attacker_name': str(row[3]) if row[3] not in (None, '') else str(attacker_id),
            'defender_id': defender_id,
            'defender_name': str(row[5]) if row[5] not in (None, '') else defender_id,
            'defender_faction': str(row[6]) if row[6] not in (None, '') else '',
            'result': str(row[7]) if row[7] is not None else '',
            'respect_gain': respect,
            'direction': direction,
        })

    df = pd.DataFrame(cleaned, columns=[
        'id', 'timestamp_started', 'attacker_id', 'attacker_name', 'defender_id',
        'defender_name', 'defender_faction', 'result', 'respect_gain', 'direction',
    ])
    return df, warnings


def build_chain(df, direction='out', successful_only=False, faction_names=None):
    """Newest-first attack log for one direction.

    Rows: {id, ts, attacker_id, attacker_name, defender_id, defender_name,
    defender_faction, defender_faction_name, result, respect_gain,
    is_success}. `successful_only` keeps only results that burn defender
    energy (SUCCESS_RESULTS). `faction_names` maps faction ID -> display name.
    """
    sub = df[df['direction'] == direction].copy()
    if successful_only:
        sub = sub[sub['result'].isin(SUCCESS_RESULTS)]

    names = faction_names or {}
    rows = []
    for _, r in sub.sort_values('timestamp_started', ascending=False).iterrows():
        rows.append({
            'id': r['id'],
            'ts': r['timestamp_started'],
            'ts_human': _fmt_ts(r['timestamp_started']),
            'attacker_id': r['attacker_id'],
            'attacker_name': r['attacker_name'],
            'defender_id': r['defender_id'],
            'defender_name': r['defender_name'],
            'defender_faction': r['defender_faction'],
            'defender_faction_name': names.get(str(r['defender_faction']), ''),
            'result': r['result'],
            'respect_gain': float(r['respect_gain']),
            'respect_color': _respect_color(r['respect_gain']),
            'is_success': r['result'] in SUCCESS_RESULTS,
            'dogpile': False,
        })
    return rows


def detect_group_attacks(df, window_s=60, min_attackers=2):
    """Find dogpiles: >= min_attackers distinct enemy attackers hitting the
    same NPO target within `window_s` seconds (first-to-last, inclusive).

    Only successful incoming hits count. Returns newest-first events:
    {target_id, target_name, start_ts, end_ts, hit_count, attackers:[...]}.
    """
    if df.empty:
        return []
    sub = df[(df['direction'] == 'in') & (df['result'].isin(SUCCESS_RESULTS))].copy()
    sub = sub[sub['defender_id'].astype(str) != '']
    if sub.empty:
        return []
    sub = sub.sort_values('timestamp_started')

    events = []
    for target_id, group in sub.groupby('defender_id', sort=False):
        rows = group.to_dict('records')
        i = 0
        while i < len(rows):
            j = i
            while j < len(rows) and rows[j]['timestamp_started'] - rows[i]['timestamp_started'] <= window_s:
                j += 1
            window_rows = rows[i:j]

            attackers = {}
            for r in window_rows:
                aid = str(r['attacker_id'])
                entry = attackers.setdefault(aid, {'id': aid, 'name': r['attacker_name'], 'hits': 0})
                entry['hits'] += 1

            if len(attackers) >= min_attackers:
                events.append({
                    'target_id': target_id,
                    'target_name': rows[i]['defender_name'] or target_id,
                    'start_ts': rows[i]['timestamp_started'],
                    'end_ts': rows[j - 1]['timestamp_started'],
                    'start_human': _fmt_ts(rows[i]['timestamp_started']),
                    'end_human': _fmt_ts(rows[j - 1]['timestamp_started']),
                    'hit_count': len(window_rows),
                    'attack_ids': [r['id'] for r in window_rows],
                    'attackers': sorted(attackers.values(), key=lambda a: (-a['hits'], a['name'])),
                })
                i = j
            else:
                i += 1

    events.sort(key=lambda e: e['start_ts'], reverse=True)
    return events


def build_heatmap(df, window_start, window_end, bucket_minutes, max_cells=20000):
    """Per-enemy-attacker hit counts bucketed over the war window.

    Counts ALL incoming attacks (any result) — activity signal. Granularity
    auto-raises (bucket size doubles) if attacker x bucket cells would exceed
    max_cells. Returns:
    {bucket_minutes, n_buckets, start, end, rows:[{attacker_id, attacker_name,
    total, buckets:[counts]}]} sorted by total desc.
    """
    sub = df[df['direction'] == 'in'].copy()
    if sub.empty:
        return {'bucket_minutes': bucket_minutes, 'n_buckets': 0, 'start': None,
                'end': None, 'rows': []}

    start = float(window_start) if window_start else sub['timestamp_started'].min()
    end = float(window_end) if window_end else sub['timestamp_started'].max()
    if end < start:
        end = start
    span = max(end - start, 1)

    n_attackers = sub['attacker_id'].nunique()
    bucket_seconds = bucket_minutes * 60
    n_buckets = max(1, int(math.ceil(span / bucket_seconds)))
    while n_attackers * n_buckets > max_cells and bucket_seconds < 24 * 3600:
        bucket_seconds *= 2
        bucket_minutes = int(bucket_seconds / 60)
        n_buckets = max(1, int(math.ceil(span / bucket_seconds)))

    grouped = sub.groupby(['attacker_id', 'attacker_name'])['timestamp_started'].agg(list)
    rows = []
    for (attacker_id, attacker_name), timestamps in grouped.items():
        buckets = [0] * n_buckets
        for ts in timestamps:
            idx = int((ts - start) / bucket_seconds)
            if idx >= n_buckets:
                idx = n_buckets - 1  # ts == window end lands in the last bucket
            if idx >= 0:
                buckets[idx] += 1
        rows.append({
            'attacker_id': attacker_id,
            'attacker_name': attacker_name,
            'total': len(timestamps),
            'buckets': buckets,
        })

    rows.sort(key=lambda r: r['total'], reverse=True)
    max_bucket = max((max(r['buckets']) for r in rows), default=0)
    return {
        'bucket_minutes': bucket_minutes,
        'n_buckets': n_buckets,
        'start': start,
        'end': end,
        'max_bucket': max_bucket,
        'labels': [
            datetime.fromtimestamp(start + i * bucket_seconds, tz=timezone.utc).strftime('%m-%d %H:%M')
            for i in range(n_buckets)
        ],
        'rows': rows,
    }


def _respect_color(value):
    """Respect display color: green scaled light -> dark (dark at +20), red at <= 0."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return '#ff5a5a'
    lightness = 72 - min(v, 20.0) / 20.0 * 34
    return f'hsl(150, 62%, {lightness:.0f}%)'


def _fmt_ts(ts):
    if not ts:
        return ''
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (TypeError, ValueError, OSError):
        return str(ts)


def _clamp_int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _tab_fields(request):
    """Tab-specific form controls (round-tripped so the form keeps state)."""
    return {
        'successful_only': request.POST.get('successful_only') == 'on',
        'def_successful_only': request.POST.get('def_successful_only') == 'on',
        'dogpile_window': _clamp_int(request.POST.get('dogpile_window'), 60, 1, 3600),
        'dogpile_min': _clamp_int(request.POST.get('dogpile_min'), 2, 2, 50),
        'heatmap_bucket': _clamp_int(request.POST.get('heatmap_bucket'), 60, 15, 1440),
    }


def _faction_names(post):
    """Faction ID -> name map gathered client-side from /faction/wars."""
    try:
        data = json.loads(post.get('faction_names', '{}'))
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _compute_tabs(df, fields, war_start, war_end, limit_24h, limit_48h,
                  defender_faction, show_tickets, faction_names=None):
    """Per-tab computation over the shared normalized DataFrame.

    Each tab is computed independently so a failure in one renders a
    per-tab error line instead of killing the whole page.
    Returns (patch, errors).
    """
    patch = {}
    errors = {}

    try:
        patch['results'] = compute_stats(df, limit_24h, limit_48h, defender_faction, show_tickets)
    except Exception as e:
        errors['tab_error_hitlimits'] = str(e)

    try:
        patch['chain'] = build_chain(df, 'out', fields['successful_only'], faction_names)
    except Exception as e:
        errors['tab_error_chain'] = str(e)

    try:
        patch['def_chain'] = build_chain(df, 'in', fields['def_successful_only'], faction_names)
    except Exception as e:
        errors['tab_error_defenders'] = str(e)

    try:
        events = detect_group_attacks(df, fields['dogpile_window'], fields['dogpile_min'])
        flagged = set()
        for ev in events:
            flagged.update(ev['attack_ids'])
        if patch.get('def_chain') is not None:
            for row in patch['def_chain']:
                row['dogpile'] = row['id'] in flagged
        patch['dogpile'] = events
        patch['heatmap'] = build_heatmap(df, war_start, war_end, fields['heatmap_bucket'])
    except Exception as e:
        errors['tab_error_defenders'] = errors.get('tab_error_defenders') or str(e)

    return patch, errors


def index_view(request):
    context = {
        'limit_24h': 10,
        'limit_48h': 15,
        'defender_faction': '',
        'show_tickets': False,
        'successful_only': False,
        'def_successful_only': True,
        'dogpile_window': 60,
        'dogpile_min': 2,
        'heatmap_bucket': 60,
        'auto_refresh': False,
        'results': None,
        'chain': None,
        'def_chain': None,
        'dogpile': None,
        'heatmap': None,
        'tab_error_hitlimits': None,
        'tab_error_chain': None,
        'tab_error_defenders': None,
        'error': None,
        'meta': None,
        'war_start_ts': '',
        'war_end_ts': '',
        'war_name': '',
        'war_opponent': '',
        'manual_start': '',
        'manual_end': '',
    }

    if request.method == 'POST':
        # KEEP STATE: Grab the values so we can pass them right back to the form
        limit_24h = int(request.POST.get('limit_24h', 15))
        limit_48h = int(request.POST.get('limit_48h', 25))
        defender_faction = request.POST.get('defender_faction', '').strip()
        show_tickets = request.POST.get('show_tickets') == 'on'
        auto_refresh = request.POST.get('auto_refresh') == 'on'
        fields = _tab_fields(request)

        # Update context immediately so the form doesn't clear if there's an error
        context.update({
            'limit_24h': limit_24h,
            'limit_48h': limit_48h,
            'defender_faction': defender_faction,
            'show_tickets': show_tickets,
            'auto_refresh': auto_refresh,
            'successful_only': fields['successful_only'],
            'def_successful_only': fields['def_successful_only'],
            'dogpile_window': fields['dogpile_window'],
            'dogpile_min': fields['dogpile_min'],
            'heatmap_bucket': fields['heatmap_bucket'],
        })

        try:
            # Live state round-trip (hidden inputs stay populated for Refresh)
            context.update({
                'war_start_ts': request.POST.get('war_start', ''),
                'war_end_ts': request.POST.get('war_end', ''),
                'war_name': request.POST.get('war_name', ''),
                'war_opponent': request.POST.get('war_opponent', ''),
                'manual_start': request.POST.get('manual_start', ''),
                'manual_end': request.POST.get('manual_end', ''),
            })

            try:
                rows = json.loads(request.POST.get('attacks', '[]'))
            except (TypeError, ValueError):
                raise ValueError("Invalid live attack payload — try refreshing the page and re-running.")

            df, warnings = normalize_attacks(rows)
            faction_names = _faction_names(request.POST)
            patch, tab_errors = _compute_tabs(
                df, fields,
                context['war_start_ts'] or df['timestamp_started'].min(),
                context['war_end_ts'] or None,
                limit_24h, limit_48h, defender_faction, show_tickets, faction_names,
            )
            context.update(patch)
            context.update(tab_errors)
            context['meta'] = {
                'fetched': len(df) + warnings,
                'warnings': warnings,
                'window_start': _fmt_ts(request.POST.get('war_start', '')) or _fmt_ts(df['timestamp_started'].min()),
                'window_end': _fmt_ts(request.POST.get('war_end', '')) or 'now',
                'war': context['war_name'],
                'restored': request.POST.get('restored') == '1',
            }

        except Exception as e:
            context['error'] = f"Error: {str(e)}"
            print(f"Error: {e}")

    return render(request, 'tracker/index.html', context)
