CREATE TABLE review_import (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1)
);

CREATE TABLE review_queue (
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    candidate_sha TEXT NOT NULL,
    draft_id TEXT,
    draft_revision INTEGER,
    queued_at TEXT NOT NULL,
    card_json TEXT NOT NULL,
    impact_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (project_id, task_id),
    UNIQUE (project_id, draft_id, draft_revision),
    FOREIGN KEY (project_id, draft_id, draft_revision) REFERENCES drafts(project_id, draft_id, revision)
);

CREATE TABLE review_decisions (
    event_id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    candidate_sha TEXT NOT NULL,
    draft_id TEXT,
    draft_revision INTEGER,
    queued_at TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'return')),
    actor_id TEXT NOT NULL,
    comment TEXT NOT NULL,
    card_json TEXT NOT NULL,
    impact_json TEXT NOT NULL DEFAULT '{}',
    published_change_id TEXT UNIQUE REFERENCES changesets(change_id),
    compensated_by TEXT UNIQUE REFERENCES changesets(change_id),
    UNIQUE (project_id, task_id, candidate_sha),
    UNIQUE (project_id, draft_id, draft_revision),
    FOREIGN KEY (project_id, draft_id, draft_revision) REFERENCES drafts(project_id, draft_id, revision)
);

CREATE INDEX review_queue_age ON review_queue(project_id, queued_at);

CREATE TABLE knowledge_cards (
    scope TEXT NOT NULL,
    knowledge_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    statement TEXT NOT NULL,
    source TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'superseded', 'archived')),
    effective_on TEXT NOT NULL,
    review_on TEXT NOT NULL,
    author TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    PRIMARY KEY (scope, knowledge_id, revision),
    FOREIGN KEY (scope, knowledge_id, revision) REFERENCES document_revisions(project_id, doc_id, version)
);

CREATE TABLE impact_links (
    project_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    check_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    result_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, rule_id, task_id, check_id, run_id, result_id)
);
