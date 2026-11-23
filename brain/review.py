"""Record candidate feedback and operator approvals for a review task."""

from __future__ import annotations

import re
from threading import Lock
from typing import NamedTuple


class FeedbackCard(NamedTuple):
    delivery_id: str
    task_id: str
    candidate_sha: str
    text: str
    previous_id: str | None = None


def _validate_sha(candidate_sha: str) -> None:
    if not isinstance(candidate_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", candidate_sha):
        raise ValueError("candidate_sha must contain exactly 40 hexadecimal digits")


class ReviewLedger:
    """Keep feedback in delivery order and approvals scoped to a task and SHA."""

    def __init__(self) -> None:
        self._feedback_by_delivery: dict[str, FeedbackCard] = {}
        self._approvals: dict[tuple[str, str], str] = {}
        self._lock = Lock()

    @property
    def cards(self) -> tuple[FeedbackCard, ...]:
        with self._lock:
            return tuple(self._feedback_by_delivery.values())

    def record_feedback(
        self,
        delivery_id: str,
        *,
        task_id: str,
        candidate_sha: str,
        text: str,
        previous_id: str | None = None,
    ) -> FeedbackCard:
        _validate_sha(candidate_sha)
        card = FeedbackCard(delivery_id, task_id, candidate_sha, text, previous_id)
        with self._lock:
            existing = self._feedback_by_delivery.get(delivery_id)
            if existing is not None:
                if existing != card:
                    raise ValueError("delivery_id was already used for different feedback")
                return existing

            if previous_id is not None:
                previous = self._feedback_by_delivery.get(previous_id)
                if previous is None or previous.task_id != task_id:
                    raise ValueError("previous_id must reference feedback for the same task")

            self._feedback_by_delivery[delivery_id] = card
            return card

    def approve(self, task_id: str, candidate_sha: str, *, operator: str) -> None:
        _validate_sha(candidate_sha)
        with self._lock:
            self._approvals[task_id, candidate_sha] = operator

    def is_approved(self, task_id: str, candidate_sha: str) -> bool:
        _validate_sha(candidate_sha)
        with self._lock:
            return (task_id, candidate_sha) in self._approvals
