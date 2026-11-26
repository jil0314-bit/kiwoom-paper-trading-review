"""Read-only header data, day boundaries and missing data; no network or engine startup."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

CORE = Path(__file__).resolve().parents[1] / 'SOURCE/core'
sys.path.insert(0, str(CORE))
import v7614_chart_panel as chart

NOW = datetime(2026, 11, 25, 10, 30, tzinfo=chart.KST)


class HeaderTests(unittest.TestCase):
    def values(self, **kw):
        return dict(dict(change_pct=2.5, volume=1500, previous_volume=1000, timestamp=NOW), **kw)

    def test_ratio_is_percent_of_whole_previous_day(self):
        text, color = chart.format_market_summary(self.values(), NOW)
        self.assertIn('▲+2.50%', text)
        self.assertIn('1,500주', text)
        self.assertIn('전일전체 대비 150.0%', text)
        self.assertEqual(color, chart.UP)

    def test_down_flat_and_zero_volume(self):
        text, color = chart.format_market_summary(self.values(change_pct=-1.2, volume=0), NOW)
        self.assertIn('▼-1.20%', text)
        self.assertIn('0.0%', text)
        self.assertEqual(color, chart.DOWN)
        self.assertEqual(chart.format_market_summary(self.values(change_pct=0), NOW)[1], chart.FLAT)

    def test_missing_or_zero_previous_volume_is_not_zero_percent(self):
        for previous in (None, 0, -1, float('nan'), float('inf')):
            text, _ = chart.format_market_summary(self.values(previous_volume=previous), NOW)
            self.assertIn('전일전체 대비 미확인', text)

    def test_invalid_current_numbers_are_unknown(self):
        text, _ = chart.format_market_summary(self.values(change_pct=float('nan'), volume=-10), NOW)
        self.assertIn('전일比 미확인', text)
        self.assertIn('거래량 미확인', text)

    def test_old_day_future_and_missing_stamp(self):
        for stamp in (None, NOW.replace(tzinfo=None), NOW-timedelta(days=1), NOW+timedelta(minutes=1)):
            text, _ = chart.format_market_summary(self.values(timestamp=stamp), NOW)
            self.assertNotIn('1,500', text)
            self.assertIn('미확인', text)

    def test_delayed_data_keeps_its_actual_timestamp(self):
        text, _ = chart.format_market_summary(self.values(timestamp=NOW-timedelta(seconds=40)), NOW)
        self.assertIn('시세지연', text)
        self.assertIn('10:29:20', text)

    def reader(self, quote, prior=None):
        engine = SimpleNamespace(latest_quotes={'005930': quote},
            _v7609_prebuy_daily_cache={('005930', NOW.date()): (NOW, prior)}, client=Mock())
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        source = ast.parse((CORE/'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_v7614_quote_summary')
        ns = {'Any': object, 'datetime': Clock, 'KST': chart.KST, '_V7614_ENGINE': {'engine': engine}}
        # Use a Clock instance because the function checks datetime type.
        if quote is not None and isinstance(quote.timestamp, datetime):
            quote.timestamp = Clock.fromisoformat(quote.timestamp.isoformat())
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual-header-reader>', 'exec'), ns)
        result = ns[method.name]('005930')
        self.assertEqual(engine.client.mock_calls, [])
        return result

    def test_reader_uses_only_existing_today_caches(self):
        quote = SimpleNamespace(symbol='005930', timestamp=NOW, change_pct=2.5, cumulative_volume=1500)
        result = self.reader(quote, ('2026-11-24', 1000, 60000))
        self.assertEqual(result['previous_volume'], 1000)
        self.assertEqual(result['volume'], 1500)

    def test_reader_rejects_wrong_symbol_and_previous_day_quote(self):
        for symbol, stamp in (('000660', NOW), ('005930', NOW-timedelta(days=1))):
            self.assertEqual(self.reader(SimpleNamespace(symbol=symbol, timestamp=stamp)), {})

    def test_reader_missing_prior_does_not_invent_a_ratio(self):
        quote = SimpleNamespace(symbol='005930', timestamp=NOW, change_pct=0, cumulative_volume=0)
        self.assertNotIn('previous_volume', self.reader(quote))

    def test_symbol_switch_and_getter_error_cannot_retain_previous_stock(self):
        panel = SimpleNamespace(symbol='005930', summary_getter=lambda s: self.values(),
            summary_var=Mock(), summary_label=Mock())
        chart.ChartPanel._update_summary(panel)
        panel.symbol = '000660'
        panel.summary_getter = Mock(side_effect=RuntimeError('missing'))
        chart.ChartPanel._update_summary(panel)
        self.assertIn('미확인', panel.summary_var.set.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
