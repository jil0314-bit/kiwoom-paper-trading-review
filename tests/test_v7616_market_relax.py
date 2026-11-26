"""V7616 패턴매수 경로 시장보류(하락주의·회복대기) 통과 시험."""
import sys, types, unittest, subprocess
from pathlib import Path
from unittest import mock
_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
CORE = Path(__file__).resolve().parents[1] / "SOURCE" / "core"
if not CORE.exists():
    CORE = Path("/home/claude/t13/ROOT/SOURCE/core")
sys.path.insert(0, str(CORE))
import bot_v76_00_three_stage_ctrlv_paper as engine


class Log:
    def __getattr__(self, name): return lambda *a, **k: None


class RelaxTests(unittest.TestCase):
    def check(self, reason, flag):
        item = object.__new__(engine.V7600ThreeStageEngine)
        item.logger = Log()
        engine._V7616_CONTEXT.route_entry = flag
        try:
            with mock.patch.object(engine._PARENT_ENGINE, "_market_entry_allowed", return_value=(False, reason)):
                return item._market_entry_allowed()
        finally:
            engine._V7616_CONTEXT.route_entry = False

    def test_caution_and_recovery_pass_on_pattern_route(self):
        self.assertTrue(self.check("시장신규매수보류_CAUTION_지수하락진행 일=+1.33%", True)[0])
        self.assertTrue(self.check("시장신규매수보류_RECOVERY_WAIT_지수하락멈춤확인중_0/20초", True)[0])

    def test_shock_and_data_halt_still_block(self):
        self.assertFalse(self.check("시장신규매수보류_SHOCK_지수급락진행", True)[0])
        self.assertFalse(self.check("시장신규매수보류_DATA_HALT_지수오류", True)[0])

    def test_other_paths_unchanged(self):
        self.assertFalse(self.check("시장신규매수보류_CAUTION_지수하락진행", False)[0])

    def test_flag_reset_after_prebuy(self):
        self.assertFalse(getattr(engine._V7616_CONTEXT, "route_entry", False))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
