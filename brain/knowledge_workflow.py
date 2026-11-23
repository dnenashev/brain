from collections.abc import Callable, Iterable
from datetime import date

from .knowledge import KnowledgeRevision, transition


def ingest(
    *,
    knowledge_id: str,
    statement: str,
    source: str,
    scope: str,
    owner: str,
    effective_on: date,
    review_on: date,
    author: str,
) -> KnowledgeRevision:
    return KnowledgeRevision(
        knowledge_id=knowledge_id,
        revision=1,
        statement=statement,
        source=source,
        scope=scope,
        owner=owner,
        status="draft",
        effective_on=effective_on,
        review_on=review_on,
        author=author,
        changed_by=author,
    )


def lint(
    card: KnowledgeRevision,
    *,
    today: date,
    source_exists: Callable[[str], bool],
) -> dict[str, str | list[str]]:
    issues = []
    if not source_exists(card.source):
        issues.append("broken_source")
    if card.review_on < today:
        issues.append("review_overdue")
    return {"owner": card.owner, "issues": issues}


def publish(
    card: KnowledgeRevision,
    *,
    today: date,
    source_exists: Callable[[str], bool],
    changed_by: str,
) -> KnowledgeRevision:
    issues = lint(card, today=today, source_exists=source_exists)["issues"]
    if issues:
        raise ValueError(", ".join(issues))
    return transition(card, "active", changed_by=changed_by)


def query(
    cards: Iterable[KnowledgeRevision], *, scope: str, today: date
) -> list[KnowledgeRevision]:
    return [
        card
        for card in cards
        if card.status == "active" and card.scope == scope and card.effective_on <= today
    ]
