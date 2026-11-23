def resolve_rule(cards, *, knowledge_id, scope, today, read_source, record_read):
    selected = None
    conflict = False
    for card in cards:
        if (
            card.knowledge_id != knowledge_id
            or card.scope != scope
            or card.status != "active"
            or card.effective_on > today
        ):
            continue
        if selected is None or card.revision > selected.revision:
            selected = card
            conflict = False
        elif card.revision == selected.revision and card != selected:
            conflict = True

    if selected is None:
        return {"outcome": "needs_clarification", "reason": "no_applicable_rule"}
    if conflict:
        return {"outcome": "needs_clarification", "reason": "conflicting_rules"}

    fragment = None
    try:
        fragment = read_source(selected.source, selected.revision)
    except FileNotFoundError:
        pass
    finally:
        available = bool(fragment)
        record_read({
            "source": selected.source,
            "revision": selected.revision,
            "available": available,
        })

    if not available:
        return {"outcome": "needs_clarification", "reason": "source_unavailable"}
    return {
        "outcome": "result",
        "statement": selected.statement,
        "source": selected.source,
        "revision": selected.revision,
        "fragment": fragment,
    }
