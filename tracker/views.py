import json
from datetime import datetime, timezone

from django.shortcuts import render
import pandas as pd

VALID_RESULTS = ('Attacked', 'Hospitalized', 'Assist', 'Lost')


def compute_stats(df, limit_24h, limit_48h, defender_faction, show_tickets, attacker_label=''):
    """Pure per-member stats over a normalized attacks DataFrame.

    Shared seam for both CSV and live modes. Input DataFrame must have
    timestamp_started, attacker_id, attacker_name, defender_faction, result
    and (optionally) respect_gain/'respect' columns.

    attacker_label: 'NPO ' for the CSV path so its original error wording
    ("No NPO attacks found...") is preserved; '' for live mode.
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
        raise ValueError(f"No {attacker_label}attacks found against '{defender_faction}'.")

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
            'tickets': tickets
        })

    # Sort by Tickets descending
    return sorted(results, key=lambda x: x['tickets'], reverse=True)


def normalize_attacks(rows):
    """Normalize compact live rows into the shared DataFrame shape.

    Each row: [id, started, attacker_id, attacker_name, defender_faction,
    result, respect_gain?] (respect_gain optional, defaults to 0).

    Returns (df, warnings): malformed rows are dropped and counted; duplicate
    ids keep the first occurrence.
    """
    cleaned = []
    seen_ids = set()
    warnings = 0

    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
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
            respect = float(row[6]) if len(row) > 6 and row[6] not in (None, '') else 0.0
        except (TypeError, ValueError):
            respect = 0.0

        cleaned.append({
            'timestamp_started': started,
            'attacker_id': attacker_id,
            'attacker_name': str(row[3]) if row[3] not in (None, '') else str(attacker_id),
            'defender_faction': str(row[4]) if row[4] not in (None, '') else '',
            'result': str(row[5]) if row[5] is not None else '',
            'respect_gain': respect,
        })

    df = pd.DataFrame(cleaned, columns=[
        'timestamp_started', 'attacker_id', 'attacker_name',
        'defender_faction', 'result', 'respect_gain',
    ])
    return df, warnings


def _fmt_ts(ts):
    if not ts:
        return ''
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (TypeError, ValueError, OSError):
        return str(ts)


def index_view(request):
    context = {
        'limit_24h': 15,
        'limit_48h': 25,
        'defender_faction': '',
        'show_tickets': False,
        'mode': 'live',
        'results': None,
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
        mode = 'live' if request.POST.get('mode') == 'live' else 'csv'

        # KEEP STATE: Grab the values so we can pass them right back to the form
        limit_24h = int(request.POST.get('limit_24h', 15))
        limit_48h = int(request.POST.get('limit_48h', 25))
        defender_faction = request.POST.get('defender_faction', '').strip()
        show_tickets = request.POST.get('show_tickets') == 'on'

        # Update context immediately so the form doesn't clear if there's an error
        context.update({
            'mode': mode,
            'limit_24h': limit_24h,
            'limit_48h': limit_48h,
            'defender_faction': defender_faction,
            'show_tickets': show_tickets,
        })

        try:
            if mode == 'live':
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
                results = compute_stats(df, limit_24h, limit_48h, defender_faction, show_tickets)

                context['results'] = results
                context['meta'] = {
                    'fetched': len(df) + warnings,
                    'warnings': warnings,
                    'window_start': _fmt_ts(request.POST.get('war_start', '')) or _fmt_ts(df['timestamp_started'].min()),
                    'window_end': _fmt_ts(request.POST.get('war_end', '')) or 'now',
                    'war': context['war_name'],
                }

            else:
                csv_file = request.FILES.get('csv_file')

                if csv_file:
                    # 1. Try reading with standard commas
                    df = pd.read_csv(csv_file)

                    # 2. Try semicolons if needed
                    if 'timestamp_started' not in df.columns:
                        csv_file.seek(0)
                        df = pd.read_csv(csv_file, sep=';')

                    if 'timestamp_started' not in df.columns:
                        raise ValueError("Could not find 'timestamp_started' column. Invalid YATA export.")

                    # Force Attacker to be NPO
                    df = df[df['attacker_factionname'].astype(str).str.contains('NPO', case=False, na=False)]

                    context['results'] = compute_stats(
                        df, limit_24h, limit_48h, defender_faction, show_tickets, attacker_label='NPO '
                    )

        except Exception as e:
            context['error'] = f"Error: {str(e)}"
            print(f"Error: {e}")

    return render(request, 'tracker/index.html', context)
