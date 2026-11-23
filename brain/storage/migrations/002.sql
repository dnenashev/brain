CREATE TABLE changesets (
    change_id TEXT NOT NULL PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,
    before_state TEXT NOT NULL,
    receipt TEXT NOT NULL,
    compensates TEXT REFERENCES changesets (change_id),
    CHECK (compensates <> change_id)
);

CREATE TRIGGER changesets_no_replacement
BEFORE INSERT ON changesets
WHEN EXISTS (SELECT 1 FROM changesets WHERE change_id = NEW.change_id)
BEGIN
    SELECT RAISE(ABORT, 'changesets are immutable');
END;

CREATE TRIGGER changesets_no_update
BEFORE UPDATE ON changesets
BEGIN
    SELECT RAISE(ABORT, 'changesets are immutable');
END;

CREATE TRIGGER changesets_no_delete
BEFORE DELETE ON changesets
BEGIN
    SELECT RAISE(ABORT, 'changesets are immutable');
END;

PRAGMA user_version = 2;
