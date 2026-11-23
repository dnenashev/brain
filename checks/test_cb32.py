"""Independent review history and candidate identity checks."""

import unittest

from brain.review import ReviewLedger


A = "a" * 40
B = "b" * 40


class ReviewHistory(unittest.TestCase):
    def test_delivery_retry_and_correction_preserve_original_card(self):
        ledger = ReviewLedger()
        one = ledger.record_feedback("delivery-1", task_id="T1", candidate_sha=A, text="Boundary case failed")
        retry = ledger.record_feedback("delivery-1", task_id="T1", candidate_sha=A, text="Boundary case failed")
        self.assertEqual(one, retry)
        self.assertEqual(tuple(ledger.cards), (one,))
        new = ledger.record_feedback("delivery-2", task_id="T1", candidate_sha=B, text="Boundary fixed", previous_id="delivery-1")
        self.assertEqual((new.previous_id, new.candidate_sha, one.candidate_sha), ("delivery-1", B, A))
        self.assertEqual(tuple(ledger.cards), (one, new))

    def test_approval_requires_exact_task_and_candidate(self):
        ledger = ReviewLedger()
        ledger.approve("T1", A, operator="operator")
        self.assertTrue(ledger.is_approved("T1", A))
        self.assertFalse(ledger.is_approved("T1", B))
        self.assertFalse(ledger.is_approved("T2", A))
        with self.assertRaises(ValueError):
            ledger.approve("T1", "main", operator="operator")

    def test_changed_duplicate_is_rejected(self):
        ledger = ReviewLedger()
        initial = ledger.record_feedback("delivery-1", task_id="T1", candidate_sha=A, text="Initial")
        with self.assertRaises(ValueError):
            ledger.record_feedback("delivery-1", task_id="T1", candidate_sha=A, text="Changed")
        self.assertEqual(tuple(ledger.cards), (initial,))
