-- Loading Agent / Meterless World Model - PostgreSQL schema v1
-- Run this file while connected to the dedicated World Model database.
-- Canonical source of truth: public.wm_events (append-only).
-- public.wm_entities, public.wm_observations, and public.wm_relationships
-- are current-state projections that can be rebuilt by replaying wm_events.

BEGIN;

CREATE TABLE IF NOT EXISTS public.wm_schema_migrations (
    version       INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum      TEXT NOT NULL,
    CONSTRAINT wm_schema_migrations_name_nonempty CHECK (length(btrim(name)) > 0)
);

-- Canonical append-only event log. Projection writes should happen in the same
-- transaction as the event insert so a failed mutation cannot partially apply.
CREATE TABLE IF NOT EXISTS public.wm_events (
    seq             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id        TEXT NOT NULL UNIQUE,
    namespace       TEXT NOT NULL,
    operation       TEXT NOT NULL,
    entity_id       TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_json     JSONB NOT NULL,
    detail_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT wm_events_namespace_nonempty CHECK (length(btrim(namespace)) > 0),
    CONSTRAINT wm_events_operation_nonempty CHECK (length(btrim(operation)) > 0),
    CONSTRAINT wm_events_schema_version_positive CHECK (schema_version > 0),
    CONSTRAINT wm_events_source_valid CHECK (
        jsonb_typeof(source_json) = 'object'
        AND jsonb_typeof(source_json -> 'kind') = 'string'
        AND length(btrim(source_json ->> 'kind')) > 0
        AND jsonb_typeof(source_json -> 'at') = 'string'
        AND length(btrim(source_json ->> 'at')) > 0
    ),
    CONSTRAINT wm_events_detail_object CHECK (jsonb_typeof(detail_json) = 'object'),
    CONSTRAINT wm_events_payload_object CHECK (jsonb_typeof(payload_json) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_wm_events_namespace_seq
    ON public.wm_events(namespace, seq);
CREATE INDEX IF NOT EXISTS ix_wm_events_entity
    ON public.wm_events(namespace, entity_id, seq);
CREATE INDEX IF NOT EXISTS ix_wm_events_operation
    ON public.wm_events(namespace, operation, seq);
CREATE INDEX IF NOT EXISTS ix_wm_events_occurred_at
    ON public.wm_events(namespace, occurred_at);

-- Enforce append-only history for ordinary UPDATE, DELETE, and TRUNCATE.
CREATE OR REPLACE FUNCTION public.wm_reject_event_log_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'wm_events is append-only; append a new event instead';
END;
$$;

DROP TRIGGER IF EXISTS wm_events_no_update_delete ON public.wm_events;
CREATE TRIGGER wm_events_no_update_delete
BEFORE UPDATE OR DELETE ON public.wm_events
FOR EACH ROW EXECUTE FUNCTION public.wm_reject_event_log_mutation();

DROP TRIGGER IF EXISTS wm_events_no_truncate ON public.wm_events;
CREATE TRIGGER wm_events_no_truncate
BEFORE TRUNCATE ON public.wm_events
FOR EACH STATEMENT EXECUTE FUNCTION public.wm_reject_event_log_mutation();

-- Current entity projection.
CREATE TABLE IF NOT EXISTS public.wm_entities (
    namespace       TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    name            TEXT,
    external_system TEXT,
    external_id     TEXT,
    attrs_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_json     JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    last_event_seq  BIGINT NOT NULL REFERENCES public.wm_events(seq) ON DELETE RESTRICT,
    PRIMARY KEY (namespace, entity_id),
    CONSTRAINT wm_entities_namespace_nonempty CHECK (length(btrim(namespace)) > 0),
    CONSTRAINT wm_entities_type_nonempty CHECK (length(btrim(entity_type)) > 0),
    CONSTRAINT wm_entities_attrs_object CHECK (jsonb_typeof(attrs_json) = 'object'),
    CONSTRAINT wm_entities_source_valid CHECK (
        jsonb_typeof(source_json) = 'object'
        AND jsonb_typeof(source_json -> 'kind') = 'string'
        AND length(btrim(source_json ->> 'kind')) > 0
        AND jsonb_typeof(source_json -> 'at') = 'string'
        AND length(btrim(source_json ->> 'at')) > 0
    ),
    CONSTRAINT wm_entities_external_key_complete CHECK (
        (external_system IS NULL AND external_id IS NULL)
        OR (external_system IS NOT NULL AND external_id IS NOT NULL
            AND length(btrim(external_system)) > 0 AND length(btrim(external_id)) > 0)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_wm_entities_external_key
    ON public.wm_entities(namespace, entity_type, external_system, external_id)
    WHERE external_system IS NOT NULL AND external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_wm_entities_type_name
    ON public.wm_entities(namespace, entity_type, name);

-- Latest ingested observation per source externalId. The event log preserves
-- previous versions; payload_json retains the full current observation object.
CREATE TABLE IF NOT EXISTS public.wm_observations (
    namespace       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    source_json     JSONB NOT NULL,
    payload_json    JSONB NOT NULL,
    last_event_seq  BIGINT NOT NULL REFERENCES public.wm_events(seq) ON DELETE RESTRICT,
    PRIMARY KEY (namespace, external_id),
    CONSTRAINT wm_observations_namespace_nonempty CHECK (length(btrim(namespace)) > 0),
    CONSTRAINT wm_observations_external_id_nonempty CHECK (length(btrim(external_id)) > 0),
    CONSTRAINT wm_observations_source_valid CHECK (
        jsonb_typeof(source_json) = 'object'
        AND jsonb_typeof(source_json -> 'kind') = 'string'
        AND length(btrim(source_json ->> 'kind')) > 0
        AND jsonb_typeof(source_json -> 'at') = 'string'
        AND length(btrim(source_json ->> 'at')) > 0
    ),
    CONSTRAINT wm_observations_payload_object CHECK (jsonb_typeof(payload_json) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_wm_observations_date
    ON public.wm_observations(namespace, observed_at);

-- Current relationship projection. valid_from/valid_to track when the edge is
-- true in the model; recorded_at captures when it was recorded by the system.
CREATE TABLE IF NOT EXISTS public.wm_relationships (
    namespace           TEXT NOT NULL,
    relationship_id     TEXT NOT NULL,
    from_entity_id      TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,
    to_entity_id        TEXT NOT NULL,
    valid_from          TIMESTAMPTZ NOT NULL,
    valid_to            TIMESTAMPTZ,
    source_json         JSONB NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL,
    last_event_seq      BIGINT NOT NULL REFERENCES public.wm_events(seq) ON DELETE RESTRICT,
    PRIMARY KEY (namespace, relationship_id),
    FOREIGN KEY (namespace, from_entity_id)
        REFERENCES public.wm_entities(namespace, entity_id) ON DELETE RESTRICT,
    FOREIGN KEY (namespace, to_entity_id)
        REFERENCES public.wm_entities(namespace, entity_id) ON DELETE RESTRICT,
    CONSTRAINT wm_relationships_namespace_nonempty CHECK (length(btrim(namespace)) > 0),
    CONSTRAINT wm_relationships_type_nonempty CHECK (length(btrim(relationship_type)) > 0),
    CONSTRAINT wm_relationships_valid_interval CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT wm_relationships_source_valid CHECK (
        jsonb_typeof(source_json) = 'object'
        AND jsonb_typeof(source_json -> 'kind') = 'string'
        AND length(btrim(source_json ->> 'kind')) > 0
        AND jsonb_typeof(source_json -> 'at') = 'string'
        AND length(btrim(source_json ->> 'at')) > 0
    )
);
CREATE INDEX IF NOT EXISTS ix_wm_relationships_from_type
    ON public.wm_relationships(namespace, from_entity_id, relationship_type, valid_from);
CREATE INDEX IF NOT EXISTS ix_wm_relationships_to_type
    ON public.wm_relationships(namespace, to_entity_id, relationship_type, valid_from);

-- Ingestion resume positions, e.g. source offsets or pagination tokens.
CREATE TABLE IF NOT EXISTS public.wm_pipeline_checkpoints (
    namespace       TEXT NOT NULL,
    pipeline_id     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    cursor_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    watermark_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, pipeline_id),
    CONSTRAINT wm_pipeline_checkpoints_namespace_nonempty CHECK (length(btrim(namespace)) > 0),
    CONSTRAINT wm_pipeline_checkpoints_pipeline_nonempty CHECK (length(btrim(pipeline_id)) > 0),
    CONSTRAINT wm_pipeline_checkpoints_source_nonempty CHECK (length(btrim(source_id)) > 0)
);

INSERT INTO public.wm_schema_migrations(version, name, checksum)
VALUES (1, 'initial_world_model_schema', 'manual-v1')
ON CONFLICT (version) DO NOTHING;

COMMIT;
