import argparse
import json
import os
import signal
from contextlib import ExitStack
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from brain.audit import AuditLog
from brain.live_workflow import LiveWorkflow
from brain.server import KnowledgeService, make_http_server
from brain.session import SessionRegistry
from brain.storage.changesets import ChangeSetStore
from brain.storage.review_state import ReviewState
from brain.storage.sqlite_store import SQLiteKnowledgeStore
from brain.web_api import ReviewWeb, make_review_server

from .config import authenticate, load_config, load_credentials
from .login import install_login


def _write_json(path, value):
    temporary = None
    try:
        with NamedTemporaryFile('w', dir=path.parent, encoding='utf-8', delete=False) as target:
            temporary = Path(target.name)
            json.dump(value, target)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _seed_queue(path):
    entries = json.loads(path.read_text(encoding='utf-8')) if path is not None else []
    if not isinstance(entries, list):
        raise ValueError('review_seed must contain an array')
    queue = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid review seed entry')
        card = entry.get('card', entry.get('review', entry))
        if not isinstance(card, dict):
            raise ValueError('invalid review seed card')
        card = {**card, 'project_id': entry.get('project_id')}
        project_id, task_id, sha = (card.get(key) for key in ('project_id', 'task_id', 'candidate_sha'))
        if any(not isinstance(value, str) or not value for value in (project_id, task_id, sha)):
            raise ValueError('review seed requires project_id, task_id and candidate_sha')
        if (project_id, task_id) in seen:
            raise ValueError('duplicate review task in project')
        seen.add((project_id, task_id))
        queue.append(card)
    return queue


class AuditedKnowledge(KnowledgeService):
    def __init__(self, store, changesets, sessions, audit, **options):
        super().__init__(store, changesets, sessions, **options)
        self.audit = audit

    def dispatch(self, method, path, *, token=None, body=None, trace_id=None):
        trace_id = trace_id or uuid4().hex
        started = monotonic()
        actor = self._sessions.resolve(token)
        status, result = super().dispatch(method, path, token=token, body=body, trace_id=trace_id)
        steps = {
            '/read': 'read', '/search': 'search', '/write': 'write', '/drafts': 'draft',
            '/check': 'check', '/reviews': 'review_enqueued', '/publish': 'publish',
            '/compensate': 'compensate',
        }
        if actor is not None and method == 'POST' and path in steps and (path != '/reviews' or status >= 400):
            outcome = 'ok' if status < 400 else 'failed'
            if status == 403:
                outcome = 'policy_denied'
            self.audit.append(trace_id=trace_id, project_id=actor['project_id'], step=steps[path],
                              outcome=outcome, details={'duration_ms': (monotonic() - started) * 1000})
        return status, result


def _serve(server, cleanup):
    class Handler(server.RequestHandlerClass):
        timeout = 5

    server.RequestHandlerClass = Handler
    server.daemon_threads = False
    thread = Thread(target=server.serve_forever)
    thread.start()
    cleanup.callback(thread.join)
    cleanup.callback(server.shutdown)


def run(config):
    data_dir = config['data_dir']
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ready = data_dir / 'ready.json'
    stopped = Event()
    with ExitStack() as cleanup:
        lock = cleanup.enter_context((data_dir / '.lock').open('a'))
        flock(lock, LOCK_EX | LOCK_NB)
        ready.unlink(missing_ok=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous = signal.signal(signum, lambda *_: stopped.set())
            cleanup.callback(signal.signal, signum, previous)

        records = load_credentials(config['credential_file'])

        def identify(credential):
            actor = authenticate(credential, records)
            return None if actor is None else {**actor, 'verified': True}

        sessions = SessionRegistry(identify)
        audit = AuditLog(data_dir / 'audit.db')
        cleanup.callback(audit.close)
        database = data_dir / 'knowledge.db'
        project_subjects = {}
        for _, identity in records:
            project_subjects.setdefault(identity['project_id'], set()).add(identity['actor_id'])
        store = cleanup.enter_context(SQLiteKnowledgeStore(database))
        changesets = cleanup.enter_context(ChangeSetStore(database))
        state = ReviewState(store, data_dir / 'review.json', lambda: _seed_queue(config['review_seed']), audit)
        workflow = LiveWorkflow(store, changesets, state, audit, project_subjects,
                                limits=config.get('limits'), directives=config.get('directives'))
        knowledge = AuditedKnowledge(
            store, changesets, sessions, audit, project_subjects=project_subjects, workflow=workflow,
        )
        cleanup.callback(knowledge.close)
        web = ReviewWeb(sessions, list_reviews=state.list_reviews, get_review=state.get_review,
                        record_decision=state.record_decision)
        bind = config['bind']
        api = cleanup.enter_context(make_http_server(knowledge, host=bind, port=config['http_port']))
        review = cleanup.enter_context(make_review_server(web, host=bind, port=config['review_port']))
        install_login(review, sessions)
        for server in (api, review):
            _serve(server, cleanup)
        host = f'[{bind}]' if ':' in bind else bind
        cleanup.callback(ready.unlink, missing_ok=True)
        _write_json(ready, {'knowledge_url': f'http://{host}:{api.server_address[1]}',
                            'review_url': f'http://{host}:{review.server_address[1]}', 'pid': os.getpid()})
        stopped.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    run(load_config(parser.parse_args().config))


if __name__ == '__main__':
    main()
