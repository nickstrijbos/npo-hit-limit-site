from django.test import SimpleTestCase
import pandas as pd

from .views import compute_stats, normalize_attacks

COLS = ['timestamp_started', 'attacker_id', 'attacker_name', 'defender_faction', 'result', 'respect_gain']


def make_df(rows):
    return pd.DataFrame(rows, columns=COLS)


class ComputeStatsTests(SimpleTestCase):
    """The shared seam: given a normalized DataFrame, per-member stats are correct."""

    def test_24h_48h_window_boundaries(self):
        # Windows measured from the earliest attack (war start), boundaries inclusive.
        # Hits at exactly 24h and 48h count; one second past 48h does not.
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 0),
            (86400, 1, 'A', 'Enemy', 'Attacked', 0),      # exactly 24h
            (172800, 1, 'A', 'Enemy', 'Attacked', 0),     # exactly 48h
            (172801, 1, 'A', 'Enemy', 'Attacked', 0),     # just past 48h
        ])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['hits_24h'], 2)
        self.assertEqual(result['hits_48h'], 3)

    def test_windows_measured_from_earliest_attack_not_now(self):
        # War start t=1000; a 25h-old attack still lands inside 24h of war start.
        df = make_df([
            (1000, 1, 'A', 'Enemy', 'Attacked', 0),
            (1000 + 25 * 3600, 1, 'A', 'Enemy', 'Attacked', 0),
        ])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['hits_24h'], 1)
        self.assertEqual(result['hits_48h'], 2)

    def test_over_limit_flags(self):
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 0),
            (60, 1, 'A', 'Enemy', 'Attacked', 0),
            (120, 1, 'A', 'Enemy', 'Attacked', 0),   # 3 hits > limit 2
            (0, 2, 'B', 'Enemy', 'Attacked', 0),
            (60, 2, 'B', 'Enemy', 'Attacked', 0),    # 2 hits == limit 2
        ])
        results = {r['name']: r for r in compute_stats(df, 2, 4, '', False)}
        self.assertTrue(results['A']['over_24h'])
        self.assertFalse(results['B']['over_24h'])
        self.assertFalse(results['A']['over_48h'])

    def test_attack_assist_loss_breakdown(self):
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 0),
            (1, 1, 'A', 'Enemy', 'Hospitalized', 0),
            (2, 1, 'A', 'Enemy', 'Assist', 0),
            (3, 1, 'A', 'Enemy', 'Assist', 0),
            (4, 1, 'A', 'Enemy', 'Lost', 0),
            (5, 1, 'A', 'Enemy', 'Mugged', 0),   # not a valid result
        ])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['attacks'], 2)     # Attacked + Hospitalized
        self.assertEqual(result['assists'], 2)
        self.assertEqual(result['losses'], 1)
        self.assertEqual(result['hits_24h'], 4)    # Attacked + Hospitalized + Assist (no Loss)

    def test_tickets_all_losses_count(self):
        # No 2/3-rule cap: every loss is paid at 15, regardless of assists
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 0),
            (1, 1, 'A', 'Enemy', 'Assist', 0),
            (2, 1, 'A', 'Enemy', 'Assist', 0),
            (3, 1, 'A', 'Enemy', 'Lost', 0),
            (4, 1, 'A', 'Enemy', 'Lost', 0),
            (5, 1, 'A', 'Enemy', 'Lost', 0),
            (6, 1, 'A', 'Enemy', 'Lost', 0),
            (7, 1, 'A', 'Enemy', 'Lost', 0),
        ])
        result = compute_stats(df, 15, 25, '', True)[0]
        self.assertEqual(result['tickets'], 1 * 20 + 2 * 15 + 5 * 15)  # 125

    def test_tickets_hidden_when_show_tickets_off(self):
        df = make_df([(0, 1, 'A', 'Enemy', 'Attacked', 0), (1, 1, 'A', 'Enemy', 'Assist', 0)])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['tickets'], 0)

    def test_respect_totals(self):
        # Respect sums over the whole window, including non-valid results
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 3.9),
            (1, 1, 'A', 'Enemy', 'Mugged', 1.08),
            (2, 2, 'B', 'Enemy', 'Assist', 2.5),
        ])
        results = {r['name']: r for r in compute_stats(df, 15, 25, '', False)}
        self.assertAlmostEqual(results['A']['respect'], 4.98)
        self.assertAlmostEqual(results['B']['respect'], 2.5)

    def test_respect_missing_column_defaults_to_zero(self):
        df = make_df([(0, 1, 'A', 'Enemy', 'Attacked', 0)]).drop(columns=['respect_gain'])
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['respect'], 0)

    def test_respect_alternate_column_name(self):
        df = make_df([(0, 1, 'A', 'Enemy', 'Attacked', 7.5)]).rename(columns={'respect_gain': 'respect'})
        result = compute_stats(df, 15, 25, '', False)[0]
        self.assertEqual(result['respect'], 7.5)

    def test_defender_faction_filter(self):
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 0),
            (1, 1, 'A', 'Other', 'Attacked', 0),
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
        df = make_df([(0, 1, 'A', 'Enemy', 'Mugged', 0)])
        with self.assertRaises(ValueError) as ctx:
            compute_stats(df, 15, 25, '', False)
        self.assertIn('No valid attacks found', str(ctx.exception))

    def test_results_sorted_by_tickets_desc(self):
        df = make_df([
            (0, 1, 'A', 'Enemy', 'Attacked', 0),
            (1, 2, 'B', 'Enemy', 'Attacked', 0),
            (2, 2, 'B', 'Enemy', 'Attacked', 0),
        ])
        results = compute_stats(df, 15, 25, '', True)
        self.assertEqual([r['name'] for r in results], ['B', 'A'])

    def test_results_keys(self):
        df = make_df([(0, 1, 'A', 'Enemy', 'Attacked', 1.0)])
        result = compute_stats(df, 15, 25, '', True)[0]
        self.assertEqual(
            set(result.keys()),
            {'id', 'name', 'hits_24h', 'over_24h', 'hits_48h', 'over_48h',
             'attacks', 'assists', 'losses', 'respect', 'tickets'},
        )


class NormalizeAttacksTests(SimpleTestCase):
    """Live compact rows -> shared DataFrame shape."""

    def test_valid_rows(self):
        rows = [
            [1, 1786000000, 100, 'Alice', 'Enemy', 'Attacked', 3.5],
            [2, 1786000060, 200, 'Bob', '', 'Lost', 0],
            [3, 1786000120, 300, 'Carol', 'Enemy', 'Assist'],  # respect_gain optional
        ]
        df, warnings = normalize_attacks(rows)
        self.assertEqual(warnings, 0)
        self.assertEqual(len(df), 3)
        self.assertEqual(list(df.columns), COLS)
        self.assertEqual(int(df.iloc[0]['timestamp_started']), 1786000000)
        self.assertEqual(df.iloc[0]['attacker_id'], 100)
        self.assertEqual(df.iloc[0]['defender_faction'], 'Enemy')
        self.assertEqual(df.iloc[0]['respect_gain'], 3.5)
        self.assertEqual(df.iloc[2]['respect_gain'], 0)

    def test_missing_defender_faction_becomes_empty_string(self):
        df, _ = normalize_attacks([[1, 100, 1, 'Alice', None, 'Attacked']])
        self.assertEqual(df.iloc[0]['defender_faction'], '')

    def test_malformed_rows_dropped_and_counted(self):
        rows = [
            [1, 100, 1, 'Alice', 'Enemy', 'Attacked'],
            None,                        # not a row
            [2],                         # too short
            [3, 'not-a-time', 1, 'A', '', 'Attacked'],
            [4, 100, None, 'A', '', 'Attacked'],   # bad attacker id
            [5, 100, 1, 'A', '', 'Attacked', 'junk'],  # bad respect coerced to 0, kept
        ]
        df, warnings = normalize_attacks(rows)
        self.assertEqual(warnings, 4)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[1]['respect_gain'], 0)

    def test_dedupe_by_id_keeps_first(self):
        rows = [
            [42, 100, 1, 'Alice', 'Enemy', 'Attacked'],
            [42, 999, 1, 'Alice', 'Enemy', 'Attacked'],
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
        df, _ = normalize_attacks([[1, 100, 7, '', 'Enemy', 'Attacked']])
        self.assertEqual(df.iloc[0]['attacker_name'], '7')
