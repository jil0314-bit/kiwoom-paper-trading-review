"""OBS-20260908-001: synthetic boundaries, not profitability evidence."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'SOURCE' / 'core'))
import v7595_r21_hwpx_logic as logic

KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 8, 8, tzinfo=KST)


def bar(i, *, low=10000, close=10000, high=None, open_=10000, volume=1):
    return SimpleNamespace(timestamp=START + timedelta(minutes=i), open=open_,
                           low=low, high=max(open_, close, low) if high is None else high,
                           close=close, volume=volume)


def decide(bars, now=None):
    return logic.evaluate_observation_admission(
        bars, now=now or max(b.timestamp for b in bars) + timedelta(minutes=1))


class ObservationFirstRiseTests(unittest.TestCase):
    def test_one_bar_exact_five_percent_and_low_volume(self):
        self.assertTrue(decide([bar(0, close=10500)]).allowed)

    def test_high_touch_only_is_not_enough(self):
        self.assertFalse(decide([bar(0, high=11000, close=10499)]).allowed)

    def test_uses_low_not_open_or_previous_close(self):
        self.assertTrue(decide([bar(0, low=9000, open_=11000, close=9450)]).allowed)

    def test_fifteenth_bar_inclusive(self):
        bars = [bar(i) for i in range(15)]
        bars[-1].close = bars[-1].high = 10500
        self.assertTrue(decide(bars).allowed)

    def test_sixteenth_bar_is_too_late(self):
        bars = [bar(i) for i in range(16)]
        bars[-1].close = bars[-1].high = 10500
        self.assertFalse(decide(bars).allowed)

    def test_equal_lows_do_not_restart_clock(self):
        bars = [bar(i) for i in range(30)]
        bars[-1].close = bars[-1].high = 10500
        self.assertFalse(decide(bars).allowed)

    def test_lower_low_restarts_clock(self):
        bars = [bar(i) for i in range(20)] + [bar(20, low=9000, close=9450)]
        self.assertTrue(decide(bars).allowed)

    def test_higher_low_does_not_slide_window(self):
        bars = [bar(0)] + [bar(i, low=10100, open_=10100, close=10100) for i in range(1, 20)]
        bars[-1].close = bars[-1].high = 10700
        self.assertFalse(decide(bars).allowed)

    def test_red_bars_count_and_do_not_block(self):
        bars = [bar(0), bar(1, open_=10300, close=10100), bar(2, close=10500)]
        self.assertTrue(decide(bars).allowed)

    def test_forming_and_future_bars_excluded(self):
        self.assertFalse(decide([bar(0), bar(1, close=10500), bar(2, close=12000)], START + timedelta(minutes=1, seconds=30)).allowed)

    def test_completed_bar_only_at_next_minute(self):
        bars = [bar(0, close=10500)]
        self.assertFalse(decide(bars, START + timedelta(seconds=59)).allowed)
        self.assertTrue(decide(bars, START + timedelta(minutes=1)).allowed)

    def test_stale_signal_is_not_replayed_as_new(self):
        self.assertFalse(decide([bar(0, close=10500)], START + timedelta(minutes=3)).allowed)

    def test_old_day_and_before_eight_excluded(self):
        bars = [bar(-1440, low=100), bar(-1, low=100), bar(0)]
        self.assertFalse(decide(bars).allowed)

    def test_day_reset(self):
        self.assertTrue(decide([bar(-1440, low=100), bar(0, close=10500)]).allowed)

    def test_order_is_normalized(self):
        self.assertTrue(decide([bar(1, close=10500), bar(0)]).allowed)

    def test_gap_is_not_compressed(self):
        self.assertFalse(decide([bar(0), bar(2, close=10500)]).allowed)

    def test_duplicate_time_rejected(self):
        self.assertFalse(decide([bar(0), bar(0, close=10500)]).allowed)

    def test_invalid_prices_rejected(self):
        for low in (0, -1, float('nan'), float('inf')):
            with self.subTest(low=low):
                self.assertFalse(decide([bar(0, low=low, close=10500)]).allowed)

    def test_invalid_ohlc_and_zero_volume_rejected(self):
        self.assertFalse(decide([bar(0, high=10200, close=10500)]).allowed)
        self.assertFalse(decide([bar(0, close=10500, volume=0)]).allowed)

    def test_no_backdated_admission_after_pullback(self):
        self.assertFalse(decide([bar(0, close=10500), bar(1, close=10200)]).allowed)

    def test_no_future_information_changes_prefix(self):
        bars = [bar(0, close=10500), bar(1, low=5000, close=5000, open_=5000)]
        self.assertTrue(decide(bars, START + timedelta(minutes=1)).allowed)

    def test_rule_reason_contains_anchor_count_close(self):
        result = decide([bar(0, close=10500)])
        self.assertTrue(result.reason.startswith('관찰편입='))
        for text in ('OBS-20260909-NWAVE', '상승차수1', '기준+5%', '08:00', '10,000', '10,500', '1/15'):
            self.assertIn(text, result.reason)

    def test_second_wave_exact_three_percent_from_higher_pullback_low(self):
        bars = [bar(0, close=10500),
                bar(1, low=10200, open_=10500, close=10300),
                bar(2, low=10300, open_=10300, close=10506)]
        result = decide(bars)
        self.assertTrue(result.allowed)
        self.assertIn('상승차수2·기준+3%', result.reason)
        self.assertIn('저점08:01=10,200', result.reason)

    def test_second_wave_under_three_percent_is_blocked(self):
        bars = [bar(0, close=10500), bar(1, low=10200, open_=10500, close=10300),
                bar(2, low=10300, open_=10300, close=10505)]
        self.assertFalse(decide(bars).allowed)

    def test_three_percent_before_first_five_percent_is_blocked(self):
        self.assertFalse(decide([bar(0, close=10300)]).allowed)

    def test_third_wave_not_repeated_counts_during_same_rise(self):
        bars = [bar(0, close=10500), bar(1, low=10200, open_=10500, close=10300),
                bar(2, low=10300, open_=10300, close=10506),
                bar(3, low=10500, open_=10506, close=10800),
                bar(4, low=10500, open_=10800, close=10600),
                bar(5, low=10600, open_=10600, close=10815)]
        self.assertIn('상승차수2', decide(bars[:4]).reason)
        self.assertIn('상승차수3', decide(bars).reason)
        self.assertTrue(decide(bars).allowed)

    def test_old_gap_does_not_poison_a_later_complete_rise(self):
        bars = [bar(0), bar(2, low=10100, open_=10100, close=10100),
                bar(3, low=10100, open_=10100, close=10605)]
        self.assertFalse(decide(bars[:2]).allowed)
        self.assertTrue(decide(bars).allowed)

    def test_postgap_first_rise_still_needs_five_percent(self):
        bars = [bar(0), bar(2), bar(3, close=10300)]
        self.assertFalse(decide(bars).allowed)

    def test_pullback_start_bar_is_not_same_bar_reentry(self):
        bars = [bar(0, close=11000), bar(1, low=10000, open_=11000, close=10500)]
        self.assertFalse(decide(bars).allowed)

    def test_later_wave_keeps_fifteen_bar_limit(self):
        bars = [bar(0, close=10500), bar(1, low=10200, open_=10500, close=10300)]
        bars += [bar(i, low=10200, open_=10300, close=10300) for i in range(2, 17)]
        bars[-1].close = bars[-1].high = 10506
        self.assertFalse(decide(bars).allowed)

    def test_later_wave_is_prefix_stable(self):
        bars = [bar(0, close=10500), bar(1, low=10200, open_=10500, close=10300),
                bar(2, low=10300, open_=10300, close=10506),
                bar(3, low=5000, open_=5000, close=5000)]
        self.assertEqual(decide(bars[:3]), decide(bars, START + timedelta(minutes=3)))

    def test_real_admission_method_accepts_one_bar_without_daily_request(self):
        # Compile the actual method without executing engine import side effects.
        tree = ast.parse((ROOT / 'SOURCE/core/bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == '_v7609_admit_observation_from_one_minute')
        namespace = {'Any': object, 'datetime': datetime,
                     '_V7594_EVALUATE_OBSERVATION': logic.evaluate_observation_admission}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual-observation-method>', 'exec'), namespace)
        probe = SimpleNamespace(_v7588_phase={}, _daily_volume_refs=Mock(side_effect=AssertionError('old gate')),
                                _v7588_publish=Mock())
        call = namespace[method.name]
        call(probe, '005930', [bar(0, close=10500)], START + timedelta(minutes=1))
        probe._v7588_publish.assert_called_once()
        self.assertEqual(probe._v7588_publish.call_args.args[1:3], ('OBSERVED', '관찰후보'))
        probe._v7588_phase['005930'] = 'INTEREST'
        call(probe, '005930', [bar(0, close=10500)], START + timedelta(minutes=1))
        probe._v7588_publish.assert_called_once()

        # A past rise must not create a fresh admission during its pullback.
        pullback = [bar(0, close=10500), bar(1, close=10200)]
        probe._v7588_phase.clear()
        probe._v7588_publish.reset_mock()
        call(probe, '005930', pullback, START + timedelta(minutes=2))
        probe._v7588_publish.assert_not_called()

        # Tightening new admission must not remove or downgrade existing stages.
        for stage in ('OBSERVED', 'INTEREST', 'PREBUY', 'MARKET_DEFERRED'):
            probe._v7588_phase['005930'] = stage
            call(probe, '005930', pullback, START + timedelta(minutes=2))
            self.assertEqual(probe._v7588_phase['005930'], stage)
        probe._v7588_publish.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
