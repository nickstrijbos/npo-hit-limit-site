from django.test import SimpleTestCase
import pandas as pd

from .views import (
    _respect_color,
    build_chain,
    build_heatmap,
    compute_stats,
    detect_group_attacks,
    normalize_attacks,
)

COLS = [
    'id', 'timestamp_started', 'attacker_id', 'attacker_name', 'defender_id',
    'defender_name', 'defender_faction', 'result', 'respect_gain', 'direction',
]


def attack(attacker_id, attacker_name, result='Attacked', ts=0, def_id='9001',
           def_name='EnemyGuy', def_fac='Enemy', respect=0.0, direction='out',
           attack_id=0):
    """Compact 10-column row in DataFrame column order."""
    return (attack_id, ts, attacker_id, attacker_name, def_id, def_name,
            def_fac, result, respect, direction)


def make_df(rows):
    return pd.DataFrame(rows, columns=COLS)


class ComputeStatsTests(SimpleTestCase):
    """The shared seam: given a normalized DataFrame, per-member stats are correct."""

    def test_24h_48h_window_boundaries(self):
        # Windows measured from the earliest attack (war start), boundaries inclusive.
        # Hits at exactly 24h and 48h count; one second past 48h does not.
        df = make_df([
            attack(1, 'A', ts=0),
            attack(1, 'A', ts=86400),      # exactly 24h
            attack(1, 'A', ts=172800),     # exactly 48h
            attack(1, 'A', ts=172801),     # just past 48h
        ])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['hits_24h'], 2)
        self.assertEqual(result['hits_48h'], 3)

    def test_windows_measured_from_earliest_attack_not_now(self):
        # War start t=1000; a 25h-old attack still lands inside 24h of war start.
        df = make_df([
            attack(1, 'A', ts=1000),
            attack(1, 'A', ts=1000 + 25 * 3600),
        ])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['hits_24h'], 1)
        self.assertEqual(result['hits_48h'], 2)

    def test_over_limit_flags(self):
        df = make_df([
            attack(1, 'A', ts=0),
            attack(1, 'A', ts=60),
            attack(1, 'A', ts=120),   # 3 hits > limit 2
            attack(2, 'B', ts=0),
            attack(2, 'B', ts=60),    # 2 hits == limit 2
        ])
        results = {r['name']: r for r in compute_stats(df, 2, 4, '', False)}
        self.assertTrue(results['A']['over_24h'])
        self.assertFalse(results['B']['over_24h'])
        self.assertFalse(results['A']['over_48h'])

    def test_attack_assist_loss_breakdown(self):
        df = make_df([
            attack(1, 'A', 'Attacked', ts=0),
            attack(1, 'A', 'Hospitalized', ts=1),
            attack(1, 'A', 'Assist', ts=2),
            attack(1, 'A', 'Assist', ts=3),
            attack(1, 'A', 'Lost', ts=4),
            attack(1, 'A', 'Mugged', ts=5),   # not a valid result
        ])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['attacks'], 2)     # Attacked + Hospitalized
        self.assertEqual(result['assists'], 2)
        self.assertEqual(result['losses'], 1)
        self.assertEqual(result['hits_24h'], 4)    # Attacked + Hospitalized + Assist (no Loss)

    def test_tickets_all_losses_count(self):
        # No 2/3-rule cap: every loss is paid at 15, regardless of assists
        df = make_df([
            attack(1, 'A', 'Attacked', ts=0),
            attack(1, 'A', 'Assist', ts=1),
            attack(1, 'A', 'Assist', ts=2),
            attack(1, 'A', 'Lost', ts=3),
            attack(1, 'A', 'Lost', ts=4),
            attack(1, 'A', 'Lost', ts=5),
            attack(1, 'A', 'Lost', ts=6),
            attack(1, 'A', 'Lost', ts=7),
        ])
        result = compute_stats(df, 15, 25, '', True)[0]
        self.assertEqual(result['tickets'], 1 * 20 + 2 * 15 + 5 * 15)  # 125

    def test_tickets_hidden_when_show_tickets_off(self):
        df = make_df([attack(1, 'A', 'Attacked'), attack(1, 'A', 'Assist', ts=1)])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['tickets'], 0)

    def test_respect_totals(self):
        # Respect sums over the whole window, including non-valid results
        df = make_df([
            attack(1, 'A', 'Attacked', respect=3.9),
            attack(1, 'A', 'Mugged', ts=1, respect=1.08),
            attack(2, 'B', 'Assist', ts=2, respect=2.5),
        ])
        results = {r['name']: r for r in compute_stats(df, 15, 25, '', False)}
        self.assertAlmostEqual(results['A']['respect'], 4.98)
        self.assertAlmostEqual(results['B']['respect'], 2.5)

    def test_respect_missing_column_defaults_to_zero(self):
        df = make_df([attack(1, 'A')]).drop(columns=['respect_gain'])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['respect'], 0)

    def test_respect_alternate_column_name(self):
        df = make_df([attack(1, 'A', respect=7.5)]).rename(columns={'respect_gain': 'respect'})
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['respect'], 7.5)

    def test_defender_faction_filter(self):
        df = make_df([
            attack(1, 'A', def_fac='Enemy'),
            attack(1, 'A', def_fac='Other', ts=1),
        ])
        results = compute_stats(df, 15, 25, 'Other', False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['hits_24h'], 1)

    def test_empty_input_raises(self):
        df = make_df([])
        with self.assertRaises(ValueError):
            compute_stats(df, 15, 25, '', False)
        with self.assertRaises(ValueError) as ctx:
            compute_stats(df, 15, 25, '', False)
        self.assertIn('No attacks found', str(ctx.exception))

    def test_no_valid_results_raises(self):
        df = make_df([attack(1, 'A', 'Mugged')])
        with self.assertRaises(ValueError) as ctx:
            compute_stats(df, 15, 25, '', False)
        self.assertIn('No valid attacks found', str(ctx.exception))

    def test_results_sorted_by_tickets_desc(self):
        df = make_df([
            attack(1, 'A'),
            attack(2, 'B', ts=1),
            attack(2, 'B', ts=2),
        ])
        results = compute_stats(df, 15, 25, '', True)
        self.assertEqual([r['name'] for r in results], ['B', 'A'])

    def test_results_keys(self):
        df = make_df([attack(1, 'A', respect=1.0)])
        result = compute_stats(df, 15, 25, '', True)[0]
        self.assertEqual(
            set(result.keys()),
            {'id', 'name', 'hits_24h', 'over_24h', 'hits_48h', 'over_48h',
             'attacks', 'assists', 'losses', 'respect', 'respect_color', 'tickets'},
        )


class NormalizeAttacksTests(SimpleTestCase):
    """Live compact rows -> shared DataFrame shape (contract v2)."""

    def test_valid_rows(self):
        rows = [
            [1, 1786000000, 100, 'Alice', 9001, 'EnemyGuy', 'Enemy', 'Attacked', 3.5, 'out'],
            [2, 1786000060, 200, 'Bob', 9002, 'Target', '', 'Lost', -1.2, 'in'],
            [3, 1786000120, 300, 'Carol', 9003, '', 'Enemy', 'Assist'],  # respect + direction optional
        ]
        df, warnings = normalize_attacks(rows)
        self.assertEqual(warnings, 0)
        self.assertEqual(len(df), 3)
        self.assertEqual(list(df.columns), COLS)
        self.assertEqual(int(df.iloc[0]['id']), 1)
        self.assertEqual(int(df.iloc[0]['timestamp_started']), 1786000000)
        self.assertEqual(df.iloc[0]['attacker_id'], 100)
        self.assertEqual(df.iloc[0]['defender_id'], '9001')
        self.assertEqual(df.iloc[0]['defender_name'], 'EnemyGuy')
        self.assertEqual(df.iloc[0]['defender_faction'], 'Enemy')
        self.assertEqual(df.iloc[0]['respect_gain'], 3.5)
        self.assertEqual(df.iloc[0]['direction'], 'out')
        self.assertEqual(df.iloc[1]['respect_gain'], -1.2)
        self.assertEqual(df.iloc[1]['direction'], 'in')
        self.assertEqual(df.iloc[2]['respect_gain'], 0)
        self.assertEqual(df.iloc[2]['direction'], 'out')

    def test_missing_defender_fields_default(self):
        df, _ = normalize_attacks([[1, 100, 1, 'Alice', None, None, None, 'Attacked']])
        self.assertEqual(df.iloc[0]['defender_id'], '')
        self.assertEqual(df.iloc[0]['defender_name'], '')
        self.assertEqual(df.iloc[0]['defender_faction'], '')

    def test_defender_name_falls_back_to_id(self):
        df, _ = normalize_attacks([[1, 100, 1, 'Alice', 42, '', 'Enemy', 'Attacked']])
        self.assertEqual(df.iloc[0]['defender_name'], '42')

    def test_bad_direction_dropped_and_counted(self):
        df, warnings = normalize_attacks([[1, 100, 1, 'Alice', 42, 'X', 'Enemy', 'Attacked', 0, 'sideways']])
        self.assertEqual(warnings, 1)
        self.assertTrue(df.empty)

    def test_malformed_rows_dropped_and_counted(self):
        rows = [
            [1, 100, 1, 'Alice', 42, 'X', 'Enemy', 'Attacked'],
            None,                        # not a row
            [2],                         # too short
            [3, 'not-a-time', 1, 'A', 42, 'X', '', 'Attacked'],
            [4, 100, None, 'A', 42, 'X', '', 'Attacked'],   # bad attacker id
            [5, 100, 1, 'A', 42, 'X', '', 'Attacked', 'junk'],  # bad respect coerced to 0, kept
        ]
        df, warnings = normalize_attacks(rows)
        self.assertEqual(warnings, 4)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[1]['respect_gain'], 0)

    def test_dedupe_by_id_keeps_first(self):
        rows = [
            [42, 100, 1, 'Alice', 42, 'X', 'Enemy', 'Attacked'],
            [42, 999, 1, 'Alice', 42, 'X', 'Enemy', 'Attacked'],
        ]
        df, warnings = normalize_attacks(rows)
        self.assertEqual(len(df), 1)
        self.assertEqual(int(df.iloc[0]['timestamp_started']), 100)
        self.assertEqual(warnings, 0)

    def test_empty_input(self):
        df, warnings = normalize_attacks([])
        self.assertTrue(df.empty)
        self.assertEqual(warnings, 0)
        df, warnings = normalize_attacks(None)
        self.assertTrue(df.empty)

    def test_missing_attacker_name_falls_back_to_id(self):
        df, _ = normalize_attacks([[1, 100, 7, '', 42, 'X', 'Enemy', 'Attacked']])
        self.assertEqual(df.iloc[0]['attacker_name'], '7')


class BuildChainTests(SimpleTestCase):
    """Newest-first per-direction attack log."""

    def test_newest_first(self):
        df = make_df([
            attack(1, 'A', ts=100),
            attack(2, 'B', ts=300),
            attack(3, 'C', ts=200),
        ])
        rows = build_chain(df, 'out')
        self.assertEqual([r['ts'] for r in rows], [300, 200, 100])

    def test_direction_filter(self):
        df = make_df([
            attack(1, 'A', ts=100),
            attack(2, 'B', ts=200, direction='in'),
        ])
        self.assertEqual(len(build_chain(df, 'out')), 1)
        self.assertEqual(len(build_chain(df, 'in')), 1)
        self.assertEqual(build_chain(df, 'in')[0]['attacker_id'], 2)

    def test_successful_only(self):
        df = make_df([
            attack(1, 'A', 'Attacked', ts=0),
            attack(2, 'B', 'Hospitalized', ts=1),
            attack(3, 'C', 'Assist', ts=2),
            attack(4, 'D', 'Lost', ts=3),
            attack(5, 'E', 'Mugged', ts=4),
        ])
        all_rows = build_chain(df, 'out')
        self.assertEqual(len(all_rows), 5)
        self.assertEqual(
            {r['attacker_id'] for r in all_rows if r['is_success']}, {1, 2})
        success_rows = build_chain(df, 'out', successful_only=True)
        self.assertEqual([r['attacker_id'] for r in success_rows], [2, 1])

    def test_respect_and_defender_passthrough(self):
        df = make_df([
            attack(1, 'A', 'Lost', respect=-2.5, def_id='77', def_name='Target', def_fac='Enemy'),
        ])
        row = build_chain(df, 'out')[0]
        self.assertEqual(row['defender_id'], '77')
        self.assertEqual(row['defender_name'], 'Target')
        self.assertEqual(row['defender_faction'], 'Enemy')
        self.assertEqual(row['respect_gain'], -2.5)
        self.assertFalse(row['is_success'])

    def test_faction_name_map(self):
        df = make_df([
            attack(1, 'A', def_fac='9999'),
        ])
        row = build_chain(df, 'out', faction_names={'9999': 'Enemy Faction'})[0]
        self.assertEqual(row['defender_faction_name'], 'Enemy Faction')
        self.assertEqual(build_chain(df, 'out')[0]['defender_faction_name'], '')

    def test_dogpile_flag_defaults_false(self):
        df = make_df([attack(1, 'A')])
        self.assertFalse(build_chain(df, 'out')[0]['dogpile'])

    def test_empty_input(self):
        self.assertEqual(build_chain(make_df([]), 'out'), [])


class RespectColorTests(SimpleTestCase):
    """Respect hue: light->dark green with magnitude (dark at +20), red at <= 0."""

    def test_positive_scales_light_to_dark(self):
        small = _respect_color(0.5)
        big = _respect_color(20)
        self.assertTrue(small.startswith('hsl('))
        self.assertTrue(big.startswith('hsl('))
        # Lightness decreases (darker) as value grows.
        small_l = float(small.split(',')[-1].rstrip('%)'))
        big_l = float(big.split(',')[-1].rstrip('%)'))
        self.assertGreater(small_l, big_l)
        self.assertEqual(big_l, 38)

    def test_caps_at_twenty(self):
        self.assertEqual(_respect_color(20), _respect_color(99))
        self.assertEqual(_respect_color(20), _respect_color(20.0))

    def test_zero_and_negative_are_red(self):
        self.assertEqual(_respect_color(0), '#ff5a5a')
        self.assertEqual(_respect_color(-2.5), '#ff5a5a')

    def test_chain_row_carries_color(self):
        df = make_df([
            attack(1, 'A', 'Attacked', respect=20),
            attack(2, 'B', 'Lost', respect=-1.5),
        ])
        rows = {r['attacker_id']: r for r in build_chain(df, 'out')}
        self.assertEqual(rows[1]['respect_color'], _respect_color(20))
        self.assertEqual(rows[2]['respect_color'], '#ff5a5a')


class DetectGroupAttacksTests(SimpleTestCase):
    """Dogpile detection: >= min_attackers distinct enemy attackers on the
    same NPO target within a window (inclusive boundaries)."""

    def test_two_attackers_within_window_detected(self):
        df = make_df([
            attack(1, 'A', 'Attacked', ts=1000, def_id='777', def_name='NpoGuy', direction='in', attack_id=1),
            attack(2, 'B', 'Attacked', ts=1040, def_id='777', def_name='NpoGuy', direction='in', attack_id=2),
        ])
        events = detect_group_attacks(df)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev['target_id'], '777')
        self.assertEqual(ev['target_name'], 'NpoGuy')
        self.assertEqual(ev['start_ts'], 1000)
        self.assertEqual(ev['end_ts'], 1040)
        self.assertEqual(ev['hit_count'], 2)
        self.assertEqual(len(ev['attackers']), 2)
        self.assertEqual(ev['attack_ids'], [1, 2])

    def test_window_boundary_inclusive(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='777', direction='in'),
            attack(2, 'B', ts=1060, def_id='777', direction='in'),  # exactly 60s
            attack(3, 'C', ts=1121, def_id='888', direction='in'),
            attack(4, 'D', ts=1182, def_id='888', direction='in'),  # 61s apart
        ])
        events = detect_group_attacks(df)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['target_id'], '777')

    def test_single_attacker_not_group(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='777', direction='in'),
            attack(1, 'A', ts=1020, def_id='777', direction='in'),
        ])
        self.assertEqual(detect_group_attacks(df), [])

    def test_min_attackers_configurable(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='777', direction='in'),
            attack(2, 'B', ts=1010, def_id='777', direction='in'),
        ])
        self.assertEqual(detect_group_attacks(df, min_attackers=3), [])
        self.assertEqual(len(detect_group_attacks(df, min_attackers=2)), 1)

    def test_window_configurable(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='777', direction='in'),
            attack(2, 'B', ts=1090, def_id='777', direction='in'),  # 90s apart
        ])
        self.assertEqual(detect_group_attacks(df, window_s=60), [])
        self.assertEqual(len(detect_group_attacks(df, window_s=90)), 1)

    def test_non_successful_excluded(self):
        df = make_df([
            attack(1, 'A', 'Lost', ts=1000, def_id='777', direction='in'),
            attack(2, 'B', 'Attacked', ts=1005, def_id='777', direction='in'),
            attack(3, 'C', 'Mugged', ts=1010, def_id='777', direction='in'),
            attack(4, 'D', 'Attacked', ts=1015, def_id='777', direction='in'),
        ])
        events = detect_group_attacks(df)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['hit_count'], 2)
        self.assertEqual({a['id'] for a in events[0]['attackers']}, {'2', '4'})

    def test_separate_targets_separate_events(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='777', direction='in'),
            attack(2, 'B', ts=1010, def_id='777', direction='in'),
            attack(3, 'C', ts=1005, def_id='888', direction='in'),
            attack(4, 'D', ts=1015, def_id='888', direction='in'),
        ])
        events = detect_group_attacks(df)
        self.assertEqual(len(events), 2)
        self.assertEqual({ev['target_id'] for ev in events}, {'777', '888'})

    def test_overlapping_runs_form_one_event(self):
        # A@0, B@30, A@40, C@90: the 0..40 run qualifies (2 distinct), C alone doesn't.
        df = make_df([
            attack(1, 'A', ts=0, def_id='777', direction='in'),
            attack(2, 'B', ts=30, def_id='777', direction='in'),
            attack(1, 'A', ts=40, def_id='777', direction='in'),
            attack(5, 'C', ts=90, def_id='777', direction='in'),
        ])
        events = detect_group_attacks(df)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['start_ts'], 0)
        self.assertEqual(events[0]['end_ts'], 40)
        self.assertEqual(events[0]['hit_count'], 3)
        self.assertEqual(len(events[0]['attackers']), 2)
        self.assertEqual(events[0]['attackers'][0]['id'], '1')
        self.assertEqual(events[0]['attackers'][0]['hits'], 2)

    def test_sorted_newest_first(self):
        df = make_df([
            attack(1, 'A', ts=0, def_id='777', direction='in'),
            attack(2, 'B', ts=10, def_id='777', direction='in'),
            attack(3, 'C', ts=100, def_id='888', direction='in'),
            attack(4, 'D', ts=110, def_id='888', direction='in'),
        ])
        events = detect_group_attacks(df)
        self.assertEqual([ev['start_ts'] for ev in events], [100, 0])

    def test_incoming_only(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='777', direction='out'),
            attack(2, 'B', ts=1010, def_id='777', direction='out'),
        ])
        self.assertEqual(detect_group_attacks(df), [])

    def test_missing_defender_id_excluded(self):
        df = make_df([
            attack(1, 'A', ts=1000, def_id='', direction='in'),
            attack(2, 'B', ts=1010, def_id='', direction='in'),
        ])
        self.assertEqual(detect_group_attacks(df), [])

    def test_empty_input(self):
        self.assertEqual(detect_group_attacks(make_df([])), [])


class BuildHeatmapTests(SimpleTestCase):
    """Per-enemy-attacker activity buckets over the war window."""

    def test_bucket_assignment(self):
        # Window 0..7200s, 60-min buckets -> exactly 2 buckets.
        df = make_df([
            attack(1, 'A', ts=0, direction='in'),
            attack(1, 'A', ts=3599, direction='in'),
            attack(1, 'A', ts=3600, direction='in'),
            attack(2, 'B', ts=7200, direction='in'),
        ])
        hm = build_heatmap(df, 0, 7200, 60)
        self.assertEqual(hm['n_buckets'], 2)
        rows = {r['attacker_id']: r for r in hm['rows']}
        self.assertEqual(rows[1]['buckets'], [2, 1])
        self.assertEqual(rows[2]['buckets'], [0, 1])
        self.assertEqual(rows[1]['total'], 3)

    def test_sorted_by_total_desc(self):
        df = make_df([
            attack(1, 'A', ts=0, direction='in'),
            attack(1, 'A', ts=60, direction='in'),
            attack(2, 'B', ts=120, direction='in'),
        ])
        hm = build_heatmap(df, 0, 7200, 60)
        self.assertEqual([r['attacker_id'] for r in hm['rows']], [1, 2])

    def test_all_results_counted(self):
        df = make_df([
            attack(1, 'A', 'Attacked', ts=0, direction='in'),
            attack(1, 'A', 'Lost', ts=10, direction='in'),
            attack(1, 'A', 'Mugged', ts=20, direction='in'),
        ])
        hm = build_heatmap(df, 0, 7200, 60)
        self.assertEqual(hm['rows'][0]['total'], 3)

    def test_window_fallback_to_data_bounds(self):
        df = make_df([
            attack(1, 'A', ts=500, direction='in'),
            attack(1, 'A', ts=600, direction='in'),
        ])
        hm = build_heatmap(df, '', '', 60)
        self.assertEqual(hm['n_buckets'], 1)
        self.assertEqual(hm['start'], 500)
        self.assertEqual(hm['end'], 600)

    def test_auto_granularity_raise(self):
        # 3 attackers x 4 hourly buckets = 12 cells > max_cells=10 -> granularity doubles.
        df = make_df([
            attack(i, f'A{i}', ts=j * 3600, direction='in')
            for i in range(1, 4) for j in range(4)
        ])
        hm = build_heatmap(df, 0, 4 * 3600, 60, max_cells=10)
        self.assertEqual(hm['bucket_minutes'], 120)
        self.assertEqual(hm['n_buckets'], 2)

    def test_empty_input(self):
        hm = build_heatmap(make_df([]), '', '', 60)
        self.assertEqual(hm['n_buckets'], 0)
        self.assertEqual(hm['rows'], [])

    def test_outgoing_excluded(self):
        df = make_df([
            attack(1, 'A', ts=0, direction='out'),
            attack(1, 'A', ts=10, direction='out'),
        ])
        self.assertEqual(build_heatmap(df, 0, 7200, 60)['rows'], [])
