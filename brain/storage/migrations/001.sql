CREATE TABLE document_revisions (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    text TEXT NOT NULL,
    UNIQUE (project_id, doc_id, version)
);

CREATE TABLE revision_readers (
    revision_id INTEGER NOT NULL REFERENCES document_revisions (id),
    subject TEXT NOT NULL,
    PRIMARY KEY (revision_id, subject)
) WITHOUT ROWID;

-- The store indexes only the current revision of each document, using its id as the FTS rowid.
CREATE VIRTUAL TABLE current_revision_fts USING fts5(text);

PRAGMA user_version = 1;
