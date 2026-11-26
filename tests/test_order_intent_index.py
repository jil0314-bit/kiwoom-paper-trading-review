"""Order-status index safety, with only temporary ledgers and no engine import."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'))
import v7600_order_idempotency as orders


def identity(key='one'):
    return orders.make_identity(engine_version='test', side='SELL', symbol='005930',
                                source='AUTO_HOLDING_EXIT', source_id=key)


class OrderStatusIndexTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'intents.jsonl'
        self.ledger = orders.OrderIntentLedger(self.path)

    def accepted(self, ledger=None, key='one'):
        ledger = ledger or self.ledger
        claim = ledger.claim(identity(key), {'quantity': 1})
        ledger.mark_accepting(claim)
        ledger.mark_accepted(claim, 'PAPER-'+key)
        return claim

    def test_restart_rebuilds_index_and_blocks_accepted_order(self):
        self.accepted()
        restarted = orders.OrderIntentLedger(self.path)
        with self.assertRaises(orders.DuplicateOrderIntent):
            restarted.claim(identity(), {'quantity': 1})

    def test_other_writer_new_records_are_seen_before_retry(self):
        first = self.ledger
        claim = first.claim(identity(), {'quantity': 1})
        self.assertEqual(first.latest_status(identity().order_intent_id), 'CLAIMED')
        other = orders.OrderIntentLedger(self.path)
        other.mark_accepting(claim)
        other.mark_accepted(claim, 'PAPER-one')
        self.assertEqual(first.latest_status(identity().order_intent_id), 'ACCEPTED')
        with self.assertRaises(orders.DuplicateOrderIntent):
            first.claim(identity(), {'quantity': 1})

    def test_cached_reads_parse_only_new_rows(self):
        self.accepted()
        self.ledger.latest_status(identity().order_intent_id)
        with patch.object(orders.json, 'loads', wraps=json.loads) as parse:
            for _ in range(20):
                self.assertEqual(self.ledger.latest_status(identity().order_intent_id), 'ACCEPTED')
            self.assertEqual(parse.call_count, 0)
            self.ledger.claim(identity('two'), {'quantity': 2})
            self.assertEqual(self.ledger.latest_status(identity('two').order_intent_id), 'CLAIMED')
            self.assertEqual(parse.call_count, 1)

    def test_truncated_file_does_not_keep_stale_status(self):
        self.accepted()
        self.ledger.latest_status(identity().order_intent_id)
        self.path.write_text('', encoding='utf-8')
        self.assertEqual(self.ledger.latest_status(identity().order_intent_id), '')

    def test_replaced_file_rebuilds_from_new_authoritative_records(self):
        self.accepted()
        self.ledger.latest_status(identity().order_intent_id)
        replacement = self.path.with_suffix('.replacement')
        other = orders.OrderIntentLedger(replacement)
        self.accepted(other, 'two')
        replacement.replace(self.path)
        self.assertEqual(self.ledger.latest_status(identity('two').order_intent_id), 'ACCEPTED')
        self.assertEqual(self.ledger.latest_status(identity().order_intent_id), '')

    def test_malformed_append_blocks_decisions(self):
        self.accepted()
        self.ledger.latest_status(identity().order_intent_id)
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write('{broken\n')
        with self.assertRaises(json.JSONDecodeError):
            self.ledger.claim(identity('two'), {'quantity': 2})

    def test_two_ledger_instances_cannot_claim_same_intent(self):
        ledgers = [self.ledger, orders.OrderIntentLedger(self.path)]
        claims, blocked, unexpected = [], [], []
        def submit(ledger):
            try:
                claims.append(ledger.claim(identity(), {'quantity': 1}))
            except orders.DuplicateOrderIntent:
                blocked.append(True)
            except Exception as exc:
                unexpected.append(exc)
        threads = [threading.Thread(target=submit, args=(ledger,)) for ledger in ledgers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(unexpected, [])
        self.assertEqual(len(claims), 1)
        self.assertEqual(len(blocked), 1)

    def test_failed_before_acceptance_can_retry_and_accepting_cannot(self):
        claim = self.ledger.claim(identity(), {'quantity': 1})
        self.ledger.release_before_acceptance(claim, 'before fill')
        retry = self.ledger.claim(identity(), {'quantity': 1})
        self.ledger.mark_accepting(retry)
        self.ledger.release_before_acceptance(retry, 'after boundary')
        with self.assertRaises(orders.DuplicateOrderIntent):
            self.ledger.claim(identity(), {'quantity': 1})


if __name__ == '__main__':
    unittest.main()
