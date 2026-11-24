CREATE TABLE drafts (
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    author TEXT NOT NULL,
    payload TEXT NOT NULL,
    change_id TEXT REFERENCES changesets(change_id),
    PRIMARY KEY (project_id, draft_id, revision)
);
