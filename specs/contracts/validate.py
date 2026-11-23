import re


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value):
    """Use JSON Schema's value-based definition of an integer."""
    return type(value) is int or (type(value) is float and value.is_integer())


def _string_list(value):
    return isinstance(value, list) and all(_nonblank(item) for item in value)


def validate_packet(packet):
    """Return the original v1 packet or raise ValueError."""
    if not isinstance(packet, dict):
        raise ValueError('packet must be an object')

    version = packet.get('schema_version')
    if not _integer(version) or version != 1:
        raise ValueError('schema_version must be the integer 1')
    if not _nonblank(packet.get('task_id')):
        raise ValueError('task_id must be a nonblank string')

    outcome = packet.get('outcome')
    if outcome not in ('result', 'rejected', 'needs_clarification'):
        raise ValueError('invalid outcome')

    snapshot = packet.get('rule_snapshot')
    if not isinstance(snapshot, list):
        raise ValueError('rule_snapshot must be an array')
    for index, rule in enumerate(snapshot):
        if not isinstance(rule, dict):
            raise ValueError(f'rule_snapshot[{index}] must be an object')
        if not _nonblank(rule.get('rule_id')) or not _nonblank(rule.get('source')):
            raise ValueError(f'rule_snapshot[{index}] requires nonblank rule_id and source')
        revision = rule.get('revision')
        if not (_integer(revision) or _nonblank(revision)):
            raise ValueError(f'rule_snapshot[{index}].revision must be an integer or nonblank string')

    constraints = packet.get('constraints')
    if not _string_list(constraints):
        raise ValueError('constraints must be an array of nonblank strings')
    if outcome in ('result', 'rejected') and (not snapshot or not constraints):
        raise ValueError('rule_snapshot and constraints must be nonempty for this outcome')

    if outcome == 'result' or 'candidate_sha' in packet:
        sha = packet.get('candidate_sha')
        if not isinstance(sha, str) or re.fullmatch(r'[0-9a-fA-F]{40}', sha) is None:
            raise ValueError('candidate_sha must contain exactly 40 hexadecimal digits')
    if outcome == 'rejected' or 'reason' in packet:
        if not _nonblank(packet.get('reason')):
            raise ValueError('reason must be a nonblank string')
    if outcome == 'needs_clarification' or 'missing' in packet:
        missing = packet.get('missing')
        if not _string_list(missing) or not missing:
            raise ValueError('missing must be a nonempty array of nonblank strings')

    return packet
