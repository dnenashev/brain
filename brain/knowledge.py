from dataclasses import dataclass, replace
from datetime import date


_NEXT_STATUS = {
    "draft": "active",
    "active": "superseded",
    "superseded": "archived",
    "archived": None,
}


@dataclass(frozen=True, slots=True)
class KnowledgeRevision:
    knowledge_id: str
    revision: int
    statement: str
    source: str
    scope: str
    owner: str
    status: str
    effective_on: date
    review_on: date
    author: str
    changed_by: str

    def __post_init__(self) -> None:
        for field in ("knowledge_id", "statement", "source", "scope", "owner",
                      "status", "author", "changed_by"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a nonempty string")
        if self.status not in _NEXT_STATUS:
            raise ValueError("invalid knowledge status")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        for field in ("effective_on", "review_on"):
            if type(getattr(self, field)) is not date:
                raise ValueError(f"{field} must be a date")


def transition(
    card: KnowledgeRevision, status: str, *, changed_by: str
) -> KnowledgeRevision:
    if not isinstance(card, KnowledgeRevision):
        raise ValueError("card must be a KnowledgeRevision")
    if not isinstance(status, str) or status != _NEXT_STATUS[card.status]:
        raise ValueError("invalid knowledge status transition")
    return replace(card, revision=card.revision + 1, status=status, changed_by=changed_by)
