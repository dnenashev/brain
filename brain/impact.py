"""Index the links between rules and their supporting work."""


_LINK_KINDS = ("task", "check", "run", "result")


class ImpactIndex:
    def __init__(self) -> None:
        self._by_rule: dict[str, dict[str, set[str]]] = {}
        self._by_identifier: dict[str, dict[str, set[str]]] = {
            kind: {} for kind in _LINK_KINDS
        }

    def add_rule(self, rule_id: str) -> None:
        if rule_id not in self._by_rule:
            self._by_rule[rule_id] = {kind: set() for kind in _LINK_KINDS}

    def link(
        self,
        rule_id: str,
        *,
        task_id: str,
        check_id: str | None = None,
        run_id: str | None = None,
        result_id: str | None = None,
    ) -> None:
        self.add_rule(rule_id)
        identifiers = {
            "task": task_id,
            "check": check_id,
            "run": run_id,
            "result": result_id,
        }
        for kind, identifier in identifiers.items():
            if identifier is not None:
                self._by_rule[rule_id][kind].add(identifier)
                self._by_identifier[kind].setdefault(identifier, set()).add(rule_id)

    def affected(self, rule_id: str) -> dict[str, list[str]]:
        links = self._by_rule.get(rule_id, {})
        return {f"{kind}s": sorted(links.get(kind, ())) for kind in _LINK_KINDS}

    def rules_for(self, kind: str, identifier: str) -> list[str]:
        if kind not in self._by_identifier:
            raise ValueError(f"Unknown link kind: {kind!r}")
        return sorted(self._by_identifier[kind].get(identifier, ()))

    def uncovered_rules(self) -> list[str]:
        return sorted(
            rule_id for rule_id, links in self._by_rule.items() if not links["check"]
        )
