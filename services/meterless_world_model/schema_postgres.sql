-- Loading Agent / Meterless World Model - SQLite schema v1
-- Canonical source of truth: wm_events (append-only).
-- wm_entities, wm_observations, and wm_relationships are rebuildable projections.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS wm_schema_migrations (
    version       INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    applied_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    checksum      TEXT NOT NULL
);

-- Every accepted mutation is recorded here before updating derived projections.
-- Keep this table append-only; replay this log to rebuild the projection tables.
CREATE TABLE IF NOT EXISTS wm_events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    namespace       TEXT NOT NULL,
    operation       TEXT NOT NULL,
    entity_id       TEXT,
    occurred_at     TEXT NOT NULL,
    recorded_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source_json     TEXT NOT NULL CHECK (
        json_valid(source_json)
        AND json_type(source_json) = 'object'
        AND json_type(source_json, '$.kind') IS 'text'
        AND length(trim(json_extract(source_json, '$.kind'))) > 0
        AND json_type(source_json, '$.at') IS 'text'
        AND length(trim(json_extract(source_json, '$.at'))) > 0
    ),
    detail_json     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object'),
    payload_json    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    schema_version  INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    CHECK (length(trim(namespace)) > 0),
    CHECK (length(trim(operation)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_wm_events_namespace_seq
    ON wm_events(namespace, seq);
CREATE INDEX IF NOT EXISTS ix_wm_events_entity
    ON wm_events(namespace, entity_id, seq);
CREATE INDEX IF NOT EXISTS ix_wm_events_operation
    ON wm_events(namespace, operation, seq);

CREATE TRIGGER IF NOT EXISTS wm_events_no_update
BEFORE UPDATE ON wm_events
BEGIN
    SELECT RAISE(ABORT, 'wm_events is append-only; append a new event instead');
END;

CREATE TRIGGER IF NOT EXISTS wm_events_no_delete
BEFORE DELETE ON wm_events
BEGIN
    SELECT RAISE(ABORT, 'wm_events is append-only; do not delete canonical history');
END;

-- Current entity projection. attrs_json and source_json preserve the complete
-- JSON structures used by the TypeScript implementation.
CREATE TABLE IF NOT EXISTS wm_entities (
    namespace       TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    name            TEXT,
    external_system TEXT,
    external_id     TEXT,
    attrs_json      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attrs_json) AND json_type(attrs_json) = 'object'),
    source_json     TEXT NOT NULL CHECK (
        json_valid(source_json)
        AND json_type(source_json) = 'object'
        AND json_type(source_json, '$.kind') IS 'text'
        AND length(trim(json_extract(source_json, '$.kind'))) > 0
        AND json_type(source_json, '$.at') IS 'text'
        AND length(trim(json_extract(source_json, '$.at'))) > 0
    ),
    updated_at      TEXT NOT NULL,
    last_event_seq  INTEGER NOT NULL REFERENCES wm_events(seq) ON DELETE RESTRICT,
    PRIMARY KEY (namespace, entity_id),
    CHECK (length(trim(namespace)) > 0),
    CHECK (length(trim(entity_type)) > 0),
    CHECK (
        (external_system IS NULL AND external_id IS NULL)
        OR
        (length(trim(external_system)) > 0 AND length(trim(external_id)) > 0)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_wm_entities_external_key
    ON wm_entities(namespace, entity_type, external_system, external_id)
    WHERE external_system IS NOT NULL AND external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_wm_entities_type_name
    ON wm_entities(namespace, entity_type, name);

-- Latest ingested observation by source externalId. payload_json retains all
-- optional fields so the original observation can be reconstructed.
CREATE TABLE IF NOT EXISTS wm_observations (
    namespace       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    source_json     TEXT NOT NULL CHECK (
        json_valid(source_json)
        AND json_type(source_json) = 'object'
        AND json_type(source_json, '$.kind') IS 'text'
        AND length(trim(json_extract(source_json, '$.kind'))) > 0
        AND json_type(source_json, '$.at') IS 'text'
        AND length(trim(json_extract(source_json, '$.at'))) > 0
    ),
    payload_json    TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    last_event_seq  INTEGER NOT NULL REFERENCES wm_events(seq) ON DELETE RESTRICT,
    PRIMARY KEY (namespace, external_id),
    CHECK (length(trim(namespace)) > 0),
    CHECK (length(trim(external_id)) > 0)
);
CREATE INDEX IF NOT EXISTS ix_wm_observations_date
    ON wm_observations(namespace, observed_at);

-- Relationship history is bitemporal: valid_from/valid_to describe when the
-- relationship applies in the model; recorded_at says when the model recorded it.
-- Closing an edge updates this projection only; the corresponding wm_events row
-- preserves the historical mutation.
CREATE TABLE IF NOT EXISTS wm_relationships (
    namespace           TEXT NOT NULL,
    relationship_id     TEXT NOT NULL,
    from_entity_id      TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,
    to_entity_id        TEXT NOT NULL,
    valid_from          TEXT NOT NULL,
    valid_to            TEXT,
    source_json         TEXT NOT NULL CHECK (
        json_valid(source_json)
        AND json_type(source_json) = 'object'
        AND json_type(source_json, '$.kind') IS 'text'
        AND length(trim(json_extract(source_json, '$.kind'))) > 0
        AND json_type(source_json, '$.at') IS 'text'
        AND length(trim(json_extract(source_json, '$.at'))) > 0
    ),
    recorded_at         TEXT NOT NULL,
    last_event_seq      INTEGER NOT NULL REFERENCES wm_events(seq) ON DELETE RESTRICT,
    PRIMARY KEY (namespace, relationship_id),
    FOREIGN KEY (namespace, from_entity_id)
        REFERENCES wm_entities(namespace, entity_id) ON DELETE RESTRICT,
    FOREIGN KEY (namespace, to_entity_id)
        REFERENCES wm_entities(namespace, entity_id) ON DELETE RESTRICT,
    CHECK (length(trim(namespace)) > 0),
    CHECK (length(trim(relationship_type)) > 0)
);
CREATE INDEX IF NOT EXISTS ix_wm_relationships_from_type
    ON wm_relationships(namespace, from_entity_id, relationship_type, valid_from);
CREATE INDEX IF NOT EXISTS ix_wm_relationships_to_type
    ON wm_relationships(namespace, to_entity_id, relationship_type, valid_from);

-- Resume point for batch/stream ingestion. cursor_json is source-specific and
-- intentionally flexible (offset, page token, source watermark, etc.).
CREATE TABLE IF NOT EXISTS wm_pipeline_checkpoints (
    namespace       TEXT NOT NULL,
    pipeline_id     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    cursor_json     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(cursor_json)),
    watermark_at    TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (namespace, pipeline_id),
    CHECK (length(trim(namespace)) > 0),
    CHECK (length(trim(pipeline_id)) > 0),
    CHECK (length(trim(source_id)) > 0)
);

-- init_db.py records schema version 1 in PRAGMA user_version after applying this file.
