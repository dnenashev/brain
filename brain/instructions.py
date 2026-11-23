from brain.policy import authorize


def resolve_directives(directives, *, audit):
    """Resolve directives in order of precedence, with level 1 highest."""
    ordered = sorted(directives, key=lambda directive: directive["level"])
    if not ordered:
        return {"outcome": "resolved", "value": None}

    higher = ordered[0]
    value = higher["value"]
    for lower in ordered[1:]:
        if lower["value"] != value:
            result = {
                "outcome": "OUTCOME_NONE_CLARIFICATION",
                "higher": {"level": higher["level"], "ref": higher["ref"]},
                "lower": {"level": lower["level"], "ref": lower["ref"]},
            }
            audit(result)
            return result

    return {"outcome": "resolved", "value": value}


def check_action(actor, request, *, current_revision, approval=None, directives=(), audit):
    """Enforce server policy before considering directives."""
    if not authorize(actor, request, current_revision=current_revision, approval=approval):
        result = {"outcome": "denied", "reason": "server_policy"}
        audit(result)
        return result

    return resolve_directives(directives, audit=audit)
