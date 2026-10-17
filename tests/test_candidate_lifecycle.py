"""LIFE-20260908-001 boundaries; isolated tests, not live trading evidence."""
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_state_invariants import engine, state, engine_probe, transition_request


class CandidateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.patch = patch.object(engine, 'STAGE_EVENT_DIR', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.now = datetime.now(engine.KST).replace(microsecond=0)
        self.probe = engine_probe()
        self.addCleanup(self.probe._test_order_folder.cleanup)
        self.probe.names = {'005930': '삼성전자'}
        self.probe._supply_potential = {}
        self.probe._v7588_phase = {}
        self.probe._v7588_flush = {}
        self.probe._save_supply_potential = Mock()

    def event(self, stage, minute=0):
        request = transition_request('005930', stage, f'{stage}-{minute}',
                                     self.now.replace(hour=8, minute=0, second=0) + timedelta(minutes=minute))
        return self.probe._v7609_stage_manager.apply(request)

    def persist(self):
        path = self.path / f'v7609_stage_events_{self.now:%Y%m%d}.jsonl'
        path.write_text(''.join(json.dumps(asdict(event), ensure_ascii=False) + '\n'
                                for event in self.probe._v7609_stage_manager.events), encoding='utf-8')
        return path

    def test_today_rows_remain_after_30_minutes_without_state_change(self):
        self.event(state.OBSERVED)
        item = SimpleNamespace(updated_at=self.now - timedelta(minutes=40),
                               expires_at=self.now - timedelta(minutes=10))
        self.probe._supply_potential['005930'] = item
        before = self.probe._v7609_stage_manager.events
        self.probe._purge_expired_potential()
        self.assertIs(self.probe._supply_potential['005930'], item)
        self.assertEqual(self.probe._v7609_stage_manager.events, before)
        self.probe._save_supply_potential.assert_not_called()

    def test_all_scanner_stages_survive_intraday_expiry(self):
        for stage in ('관찰후보', '관심종목', '매수직전', '시장보류'):
            item = SimpleNamespace(stage=stage, updated_at=self.now.replace(hour=8),
                                   expires_at=self.now.replace(hour=8, minute=30))
            self.probe._supply_potential['005930'] = item
            self.probe._purge_expired_potential()
            self.assertIs(self.probe._supply_potential['005930'], item)

    def test_previous_day_mirror_is_cleaned_not_today_position(self):
        position = object()
        self.probe.broker = SimpleNamespace(positions={'005930': position})
        self.probe._supply_potential['005930'] = SimpleNamespace(updated_at=self.now - timedelta(days=1))
        self.probe._purge_expired_potential()
        self.assertNotIn('005930', self.probe._supply_potential)
        self.assertIs(self.probe.broker.positions['005930'], position)
        self.probe._save_supply_potential.assert_called_once()

    def test_new_mirror_deadline_is_end_of_day_not_30_minutes(self):
        item = self.probe._v7588_make_potential('005930', '관찰후보', 'test', self.now)
        self.assertEqual(item.expires_at, self.now.replace(hour=23, minute=59, second=59, microsecond=999999))
        self.assertEqual(item.updated_at, self.now)
        self.assertEqual(item.stage, '관찰후보')

    def test_restart_loads_expired_today_mirror_but_not_yesterday(self):
        item = self.probe._v7588_make_potential('005930', '관찰후보', 'test', self.now)
        raw = asdict(item)
        raw['expires_at'] = (self.now - timedelta(minutes=1)).isoformat()
        old = dict(raw, symbol='000660', updated_at=(self.now - timedelta(days=1)).isoformat())
        path = self.path / 'potentials.json'
        path.write_text(json.dumps({'candidates': [raw, old]}, default=str), encoding='utf-8')
        scope = super(engine.V7600ThreeStageEngine, self.probe)._load_supply_potential.__func__.__globals__
        with patch.dict(scope, POTENTIAL_FILE=path):
            self.probe._load_supply_potential()
        self.assertEqual(set(self.probe._supply_potential), {'005930'})
        self.assertIsNone(self.probe._v7609_stage_manager.stage_of('005930'))
        self.assertEqual(self.probe._v7609_stage_manager.events, ())

    def test_missing_loader_file_is_not_created(self):
        path = self.path / 'missing.json'
        scope = super(engine.V7600ThreeStageEngine, self.probe)._load_supply_potential.__func__.__globals__
        with patch.dict(scope, POTENTIAL_FILE=path):
            self.probe._load_supply_potential()
        self.assertFalse(path.exists())

    def test_path_retains_removal_and_reentry_with_authoritative_times(self):
        for stage, minute in ((state.OBSERVED, 0), (state.INTEREST, 1),
                              (state.REMOVED, 2), (state.OBSERVED, 3)):
            self.event(stage, minute)
        result = self.probe._v7609_stage_path('005930', self.now)
        self.assertEqual(result.count('관찰 '), 2)
        self.assertIn('관심 08:01:01', result)
        self.assertIn('탈락 08:02:01(STATE005 관찰해제)', result)
        self.assertTrue(result.endswith('관찰 08:03:01'))

    def test_legacy_timeline_cannot_invent_display_transition(self):
        self.probe._v7591_timeline = {'005930': {'events': [{'stage': '관심',
            'at': self.now.isoformat(), 'reason': engine.V7609_INTEREST_EVENT_MARKER + 'old'}]}}
        self.assertEqual(self.probe._v7609_stage_path('005930', self.now), '')

    def test_history_reads_removed_symbol_without_modifying_files_or_state(self):
        self.event(state.OBSERVED)
        self.event(state.REMOVED, 1)
        path = self.persist()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        events = self.probe._v7609_stage_manager.events
        rows = engine._v7609_history_rows(self.now.date())
        self.assertEqual(rows[-1]['kind'], '탈락')
        self.assertEqual(rows[-1]['old_stage'], state.OBSERVED)
        self.assertEqual(rows[-1]['reason'], 'STATE005 관찰해제')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.probe._v7609_stage_manager.events, events)

    def test_history_detects_tampered_reason(self):
        self.event(state.OBSERVED)
        path = self.persist()
        path.write_text(path.read_text(encoding='utf-8').replace('STATE005 관찰', 'changed'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '해시'):
            engine._v7609_history_rows(self.now.date())

    def test_inflight_partial_tail_is_not_invented_as_event(self):
        self.event(state.OBSERVED)
        path = self.persist()
        with path.open('a', encoding='utf-8') as stream:
            stream.write('{"partial":')
        self.assertEqual(len(engine._v7609_history_rows(self.now.date())), 1)

    def test_empty_history_does_not_create_files(self):
        self.assertEqual(engine._v7609_history_rows(self.now.date()), [])
        self.assertEqual(list(self.path.iterdir()), [])

    def test_removal_records_reason_clock_and_only_once(self):
        self.event(state.OBSERVED)
        item = SimpleNamespace(updated_at=self.now - timedelta(minutes=2))
        self.assertTrue(self.probe._v7609_record_scanner_removal('005930', item, '기존 조건 해제 사유', self.now))
        self.assertFalse(self.probe._v7609_record_scanner_removal('005930', item, '기존 조건 해제 사유', self.now))
        event = self.probe._v7609_stage_manager.events[-1]
        self.assertEqual(event.decision_time, self.now.isoformat())
        self.assertEqual(event.reason, '기존 조건 해제 사유')
        self.assertEqual(len(self.probe._v7609_stage_log.events), 1)

    def test_manual_exclusion_uses_existing_path_and_records_removal(self):
        self.event(state.OBSERVED)
        self.probe._v7587_excluded = set()
        self.probe._v7587_save_exclusions = Mock()
        self.probe._save_command_state = Mock()
        self.probe._v7587_exclude_symbol('005930', '삼성전자')
        self.assertIn('005930', self.probe._v7587_excluded)
        self.assertEqual(self.probe._v7609_stage_manager.stage_of('005930'), state.REMOVED)
        self.assertEqual(self.probe._v7609_stage_manager.events[-1].reason, '사용자 명령으로 제외')

    def test_removal_cannot_replace_holding(self):
        request = transition_request('005930', state.HOLDING, 'filled', self.now, quantity=2, fill='fill1')
        self.probe._v7609_stage_manager.apply_paper_fill(request)
        self.assertFalse(self.probe._v7609_record_scanner_removal('005930', None, 'exclude', self.now))
        self.assertEqual(self.probe._v7609_stage_manager.stage_of('005930'), state.HOLDING)

    def test_wait_reason_is_not_removal_and_deduplicates_after_restart(self):
        self.event(state.OBSERVED)
        self.event(state.INTEREST, 1)
        events = self.probe._v7609_stage_manager.events
        for _ in range(2):
            self.probe._v7609_record_stage_wait('005930', 'MA5 미충족', self.now, self.now, 'BOTTOM_MA5')
        del self.probe._v7609_wait_history_cache
        self.probe._v7609_record_stage_wait('005930', 'MA5 미충족', self.now, self.now, 'BOTTOM_MA5')
        rows = engine._v7609_history_rows(self.now.date())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], '조건대기')
        self.assertEqual(rows[0]['new_stage'], state.INTEREST)
        self.assertEqual(self.probe._v7609_stage_manager.events, events)

    def test_changed_wait_reason_keeps_both_clocks(self):
        self.event(state.OBSERVED)
        self.probe._v7609_record_stage_wait('005930', 'A', self.now, self.now, 'test')
        later = self.now + timedelta(seconds=3)
        self.probe._v7609_record_stage_wait('005930', 'B', self.now, later, 'test')
        rows = engine._v7609_history_rows(self.now.date())
        self.assertEqual([row['reason'] for row in rows], ['A', 'B'])
        self.assertEqual(rows[1]['decision_time'], later.isoformat())

    def test_no_wait_record_for_unadmitted_symbol(self):
        self.probe._v7609_record_stage_wait('005930', 'A', None, self.now, 'test')
        self.assertEqual(list(self.path.iterdir()), [])

    def test_wait_io_failure_does_not_change_stage_or_raise_into_engine(self):
        self.event(state.OBSERVED)
        events = self.probe._v7609_stage_manager.events
        with patch.object(Path, 'open', side_effect=OSError('disk')):
            self.probe._v7609_record_stage_wait('005930', 'A', None, self.now, 'test')
        self.assertEqual(self.probe._v7609_stage_manager.events, events)

    def test_wait_before_state_initialization_is_a_noop(self):
        probe = object.__new__(engine.V7600ThreeStageEngine)
        probe._v7609_record_stage_wait('005930', 'A', None, self.now, 'test')
        self.assertEqual(list(self.path.iterdir()), [])

    def test_wait_partial_tail_is_preserved_without_appending_corruption(self):
        self.event(state.OBSERVED)
        path = self.path / f'v7609_stage_wait_{self.now:%Y%m%d}.jsonl'
        path.write_text('{"partial":', encoding='utf-8')
        before = path.read_bytes()
        self.probe._v7609_record_stage_wait('005930', 'A', None, self.now, 'test')
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
