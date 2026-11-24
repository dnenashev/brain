#!/usr/bin/env python3
"""Validate the Company Brain launch pack and emit one gated Swarm TaskSpec."""

import argparse
from graphlib import CycleError, TopologicalSorter
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent
CHECKLIST = ROOT.parent / "company-brain-build-checklist.md"
ITEM = re.compile(r"^- \[[ xX]\] \*\*((?:CB|O)\d+)\. (.*?)\.\*\* (.+)$", re.MULTILINE)
SAFE_PATH = re.compile(r"[a-zA-Z0-9_./*-]+")
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def is_scope(path):
    parts = path.split("/")
    return (
        SAFE_PATH.fullmatch(path)
        and parts[0] in {"brain", "deploy", "docs", "release", "specs"}
        and all(part not in {"", ".", "..", "AGENTS.md"} for part in parts)
        and ("*" not in path or path.count("*") == 2 and path.endswith("/**"))
    )


def load_pack():
    manifest = read_json(ROOT / "graph.json")
    corpus = CHECKLIST.read_text(encoding="utf-8")
    items = ITEM.findall(corpus)
    checklist = {item_id: f"{title}. {details}" for item_id, title, details in items}
    if manifest.get("schema") != 1 or (ROOT / manifest["source"]).resolve() != CHECKLIST:
        raise ValueError("Unrecognized graph schema or checklist path")
    tasks = {row["id"]: row for row in manifest["tasks"]}
    gates = {row["id"]: row for row in manifest["operator_gates"]}
    nodes = {**tasks, **gates}
    if (len(checklist) != len(items) or len(nodes) != len(manifest["tasks"]) + len(manifest["operator_gates"])
            or nodes.keys() != checklist.keys()):
        raise ValueError("Checklist IDs and graph IDs must match exactly and be unique")
    for item_id, row in nodes.items():
        dependencies = row["after"]
        if len(set(dependencies)) != len(dependencies) or not set(dependencies) <= nodes.keys():
            raise ValueError(f"Invalid dependencies for {item_id}")
    for item_id, row in tasks.items():
        if not re.fullmatch(r"CB\d+", item_id) or not row["paths"] or not all(map(is_scope, row["paths"])):
            raise ValueError(f"Invalid output scope for {item_id}")
    for item_id in gates:
        if not re.fullmatch(r"O\d+", item_id):
            raise ValueError(f"Invalid operator gate: {item_id}")
    graph = {key: row["after"] for key, row in nodes.items()}
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError("The task graph has a cycle") from exc
    fixtures = read_json(ROOT / "pilot-cases.json")
    case_ids = [row["id"] for row in fixtures["operator_expectations"]]
    if (fixtures.get("schema") != 1 or fixtures.get("data_class") != "PUBLIC"
            or fixtures.get("synthetic") is not True
            or case_ids != [f"BDD{n:02}" for n in range(1, 9)]
            or fixtures["regression"]["id"] != "REG01"):
        raise ValueError("Expected eight synthetic BDD cases and regression REG01")
    stages = len(re.findall(r"^## Этап \d+\.", corpus, re.MULTILINE))
    return tasks, gates, checklist, graph, order, stages


def read_journal(path, tasks, graph):
    if path is None:
        return {}
    payload = read_json(path)
    if payload.get("schema") != 1 or not isinstance(payload.get("confirmed"), dict):
        raise ValueError("Invalid evidence journal")
    confirmed = payload["confirmed"]
    for item_id, record in confirmed.items():
        if item_id not in graph or not isinstance(record, dict):
            raise ValueError(f"Unknown or invalid journal item: {item_id}")
        if not isinstance(record.get("evidence"), str) or not record["evidence"].strip():
            raise ValueError(f"Missing evidence reference for {item_id}")
        if not set(graph[item_id]) <= confirmed.keys():
            raise ValueError(f"Journal records {item_id} before its predecessors")
        if item_id in tasks and (
            not isinstance(record.get("merged_sha"), str) or not SHA.fullmatch(record["merged_sha"])
        ):
            raise ValueError(f"Missing exact merged SHA for {item_id}")
    return confirmed


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True)
    if result.returncode:
        raise ValueError(f"Git check failed: {' '.join(args)}")
    return result.stdout


def verify_repository(repo, task_id, graph, merged):
    repo = repo.resolve(strict=True)
    root = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repo:
        raise ValueError("--repo must name the root of the isolated integration repository")
    if git(repo, "status", "--porcelain"):
        raise ValueError("The integration repository must have a clean working tree")
    sha = git(repo, "rev-parse", "--verify", "HEAD").decode().strip()
    pending = list(graph[task_id])
    seen = set()
    while pending:
        dependency = pending.pop()
        if dependency in seen:
            continue
        seen.add(dependency)
        record = merged[dependency]
        if "merged_sha" in record:
            git(repo, "merge-base", "--is-ancestor", record["merged_sha"], sha)
        pending.extend(graph[dependency])
    protected = ["checks/__init__.py", "checks/run.py", f"checks/test_{task_id.lower()}.py"]
    for path in protected:
        entry = git(repo, "ls-tree", sha, "--", path)
        if not entry.startswith((b"100644 blob ", b"100755 blob ")):
            raise ValueError(f"Missing a protected test file in HEAD: {path}")
    if git(repo, "show", f"{sha}:checks/run.py") != (ROOT / "check_runner.py").read_bytes():
        raise ValueError("The committed checks/run.py differs from the pinned pack runner")
    return sha


def prepare_spec(args, tasks, checklist, graph, confirmed):
    task_id = args.task
    if task_id not in tasks or task_id in confirmed:
        raise ValueError(f"Task is unknown or has already been confirmed: {task_id}")
    missing = set(graph[task_id]) - confirmed.keys()
    if missing:
        raise ValueError(f"{task_id} is gated by {', '.join(sorted(missing))}")
    base = verify_repository(args.repo, task_id, graph, confirmed)
    return {
        "task_id": task_id,
        "brief": (
            f"{checklist[task_id]} Изолированный срез Company Brain. "
            "Реализуй только заявленный результат. Используй синтетические материалы. "
            "Не меняй существующие контракты, защищённые тесты и правила. "
            "Верификатор проверяет допустимые пути, поведение и достаточность свидетельств."
        ),
        "allowed_paths": tasks[task_id]["paths"],
        "forbidden_paths": ["checks/**", "tests/acceptance/**", "holdout/**", "evals/**"],
        "verify": ["python3", "-IB", "checks/run.py", task_id],
        "dependencies": [item for item in graph[task_id] if item in tasks],
        "base_ref": base,
        "workspace_mode": "share",
        "data_class": "PUBLIC",
        "max_turns": 12,
        "max_attempts": 3,
        "timeout": 180,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    ready = commands.add_parser("ready")
    ready.add_argument("--journal", type=Path)
    spec = commands.add_parser("spec")
    spec.add_argument("task")
    spec.add_argument("--repo", type=Path, required=True)
    spec.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    try:
        tasks, gates, checklist, graph, order, stages = load_pack()
        confirmed = read_journal(getattr(args, "journal", None), tasks, graph)
        if args.command == "validate":
            result = {"stages": stages, "tasks": len(tasks), "operator_gates": len(gates), "bdd_cases": 8}
        elif args.command == "ready":
            eligible = [key for key in order if key not in confirmed and set(graph[key]) <= confirmed.keys()]
            result = {
                "swarm_tasks": [key for key in eligible if key in tasks],
                "operator_gates": [key for key in eligible if key in gates],
            }
        else:
            result = prepare_spec(args, tasks, checklist, graph, confirmed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.exit(2, f"Launch pack: {exc}\n")


if __name__ == "__main__":
    main()
