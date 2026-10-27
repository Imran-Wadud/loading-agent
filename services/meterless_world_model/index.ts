
/**
 * Loading Agent — isolated Meterless World Model implementation.
 *
 * Backends:
 *   storage: "memory"   - conformance runner / tests
 *   storage: "postgres" - persistent PostgreSQL storage
 *
 * PostgreSQL mode requires await world.ready() before synchronous reads
 * such as stateShapeCanonical(), view(), and audit().
 *
 * Environment:
 *   DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
 *
 * The database schema must be applied using schema_postgres.sql first.
 */

import { createHash, randomUUID } from "node:crypto";
import { Pool, type PoolClient } from "pg";

export type Provenance = {
  kind: string;
  at: string;
  [key: string]: unknown;
};

export type EntityInput = {
  type: string;
  name?: string;
  externalKey?: { system: string; id: string };
  attrs?: Record<string, unknown>;
  source?: Provenance;
  [key: string]: unknown;
};

export type Observation = {
  externalId: string;
  title: string;
  at: string;
  source: Provenance;
  entities?: Array<{ type: string; name: string }>;
  [key: string]: unknown;
};

export type RelationshipInput = {
  from: string;
  type: string;
  to: string;
  validFrom: string;
  source: Provenance;
};

type EntityRecord = {
  id: string;
  type: string;
  name?: string;
  externalKey?: { system: string; id: string };
  attrs: Record<string, unknown>;
  source: Provenance;
  updatedAt: string;
};

type EdgeRecord = RelationshipInput & {
  id: string;
  validTo?: string;
  recordedAt: string;
};

type AuditRecord = {
  seq: number;
  at: string;
  operation: string;
  entityId?: string;
  detail?: Record<string, unknown>;
};

type EventInput = {
  operation: string;
  entityId?: string;
  source: Provenance;
  detail?: Record<string, unknown>;
  payload?: Record<string, unknown>;
};

export function normalizeName(input: string): string {
  return String(input)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[\u00A0\s]+/g, " ")
    .trim()
    .toLowerCase();
}

function digest(input: string): string {
  return createHash("sha256")
    .update(input, "utf8")
    .digest("hex")
    .slice(0, 24);
}

export function entityIdFromName(type: string, name: string): string {
  const normalizedType = String(type).trim().toLowerCase();
  const normalizedName = normalizeName(name);

  if (!normalizedType || !normalizedName) {
    throw new Error("Entity type and name are required");
  }

  return `${normalizedType}:name:${digest(
    `${normalizedType}\u0000${normalizedName}`
  )}`;
}

export type MergeDecision = "auto-merge" | "review" | "new";

export function decide(similarity: number): MergeDecision {
  if (
    !Number.isFinite(similarity) ||
    similarity < 0 ||
    similarity > 1
  ) {
    throw new Error(
      "Similarity must be a finite number between 0 and 1"
    );
  }

  if (similarity >= 0.85) return "auto-merge";
  if (similarity >= 0.82) return "review";
  return "new";
}

function stableStringify(value: unknown): string {
  const canonical = (item: any): any => {
    if (Array.isArray(item)) return item.map(canonical);

    if (item && typeof item === "object") {
      const result: Record<string, unknown> = {};

      for (const key of Object.keys(item).sort()) {
        if (item[key] !== undefined) {
          result[key] = canonical(item[key]);
        }
      }

      return result;
    }

    return item;
  };

  return JSON.stringify(canonical(value));
}

function parseJson<T>(value: unknown): T {
  if (typeof value === "string") return JSON.parse(value) as T;
  return value as T;
}

function asIso(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value);

  if (!Number.isFinite(date.getTime())) {
    throw new Error(`Invalid timestamp: ${String(value)}`);
  }

  return date.toISOString();
}

export type WorldModelOptions = {
  storage?: string;
  namespace?: string;
  clock?: () => Date;
  connectionString?: string;
  pool?: Pool;
};

export class WorldModel {
  private readonly namespace: string;
  private readonly clock: () => Date;
  private readonly storage: "memory" | "postgres";

  private readonly entities = new Map<string, EntityRecord>();
  private readonly edges = new Map<string, EdgeRecord>();
  private readonly observations = new Map<string, Observation>();
  private readonly auditLog: AuditRecord[] = [];

  private nextSeq = 1;
  private readonly pool?: Pool;
  private ownsPool = false;
  private readyPromise?: Promise<void>;
  private initialized = false;

  constructor(options: WorldModelOptions = {}) {
    this.storage = (options.storage ?? "memory") as
      | "memory"
      | "postgres";

    if (
      this.storage !== "memory" &&
      this.storage !== "postgres"
    ) {
      throw new Error(`Unsupported storage backend: ${this.storage}`);
    }

    this.namespace = options.namespace ?? "default";
    this.clock = options.clock ?? (() => new Date());

    if (this.storage === "postgres") {
      if (options.pool) {
        this.pool = options.pool;
      } else {
        const connectionString =
          options.connectionString ?? process.env.DATABASE_URL;

        if (!connectionString) {
          throw new Error(
            "PostgreSQL storage requires DATABASE_URL or options.connectionString"
          );
        }

        this.pool = new Pool({ connectionString });
        this.ownsPool = true;
      }
    } else {
      this.initialized = true;
    }
  }

  private now(): string {
    return this.clock().toISOString();
  }

  /**
   * Load the current projections and audit log.
   * Call this after constructing a PostgreSQL-backed instance.
   */
  async ready(): Promise<void> {
    if (this.initialized) return;
    if (this.readyPromise) return this.readyPromise;

    this.readyPromise = this.loadPostgresState();

    try {
      await this.readyPromise;
      this.initialized = true;
    } catch (error) {
      this.readyPromise = undefined;
      throw error;
    }
  }

  private async loadPostgresState(): Promise<void> {
    const pool = this.requirePool();

    const [
      entityResult,
      observationResult,
      relationshipResult,
      eventResult,
    ] = await Promise.all([
      pool.query(
        `SELECT entity_id, entity_type, name, external_system,
                external_id, attrs_json, source_json, updated_at
         FROM public.wm_entities
         WHERE namespace = $1`,
        [this.namespace]
      ),

      pool.query(
        `SELECT external_id, payload_json
         FROM public.wm_observations
         WHERE namespace = $1`,
        [this.namespace]
      ),

      pool.query(
        `SELECT relationship_id, from_entity_id, relationship_type,
                to_entity_id, valid_from, valid_to, source_json,
                recorded_at
         FROM public.wm_relationships
         WHERE namespace = $1`,
        [this.namespace]
      ),

      pool.query(
        `SELECT seq, operation, entity_id, occurred_at, detail_json
         FROM public.wm_events
         WHERE namespace = $1
         ORDER BY seq`,
        [this.namespace]
      ),
    ]);

    this.entities.clear();
    this.observations.clear();
    this.edges.clear();
    this.auditLog.length = 0;

    for (const row of entityResult.rows) {
      const externalKey =
        row.external_system == null
          ? undefined
          : {
              system: row.external_system,
              id: row.external_id,
            };

      const entity: EntityRecord = {
        id: row.entity_id,
        type: row.entity_type,
        name: row.name ?? undefined,
        externalKey,
        attrs: parseJson(row.attrs_json),
        source: parseJson(row.source_json),
        updatedAt: asIso(row.updated_at),
      };

      this.entities.set(entity.id, entity);
    }

    for (const row of observationResult.rows) {
      const observation = parseJson<Observation>(row.payload_json);
      this.observations.set(row.external_id, observation);
    }

    for (const row of relationshipResult.rows) {
      const edge: EdgeRecord = {
        id: row.relationship_id,
        from: row.from_entity_id,
        type: row.relationship_type,
        to: row.to_entity_id,
        validFrom: asIso(row.valid_from),
        validTo:
          row.valid_to == null ? undefined : asIso(row.valid_to),
        source: parseJson(row.source_json),
        recordedAt: asIso(row.recorded_at),
      };

      this.edges.set(edge.id, edge);
    }

    for (const row of eventResult.rows) {
      this.auditLog.push({
        seq: Number(row.seq),
        at: asIso(row.occurred_at),
        operation: row.operation,
        entityId: row.entity_id ?? undefined,
        detail: parseJson(row.detail_json),
      });
    }

    this.nextSeq =
      this.auditLog.reduce(
        (max, record) => Math.max(max, record.seq),
        0
      ) + 1;
  }

  private requirePool(): Pool {
    if (!this.pool) {
      throw new Error("PostgreSQL pool is not configured");
    }
    return this.pool;
  }

  private requireSource(
    source: Provenance | undefined
  ): Provenance {
    if (
      !source ||
      typeof source !== "object" ||
      typeof source.kind !== "string" ||
      !source.kind.trim() ||
      typeof source.at !== "string" ||
      !Number.isFinite(Date.parse(source.at))
    ) {
      throw new Error(
        "Valid provenance is required for every world-model write"
      );
    }

    return structuredClone(source);
  }

  private idFor(input: EntityInput): string {
    if (input.externalKey) {
      const { system, id } = input.externalKey;

      if (!system?.trim() || !id?.trim()) {
        throw new Error("External key requires system and id");
      }

      return `${input.type.trim().toLowerCase()}:${system.trim()}:${id.trim()}`;
    }

    if (!input.name?.trim()) {
      throw new Error("Entity name or externalKey is required");
    }

    return entityIdFromName(input.type, input.name);
  }

  private record(
    operation: string,
    entityId?: string,
    detail?: Record<string, unknown>,
    at?: string
  ): void {
    this.auditLog.push({
      seq: this.nextSeq++,
      at: at ?? this.now(),
      operation,
      entityId,
      detail,
    });
  }

  private async appendEvent(
    client: PoolClient,
    event: EventInput
  ): Promise<number> {
    const result = await client.query(
      `INSERT INTO public.wm_events
         (event_id, namespace, operation, entity_id, occurred_at,
          source_json, detail_json, payload_json)
       VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb)
       RETURNING seq`,
      [
        randomUUID(),
        this.namespace,
        event.operation,
        event.entityId ?? null,
        event.source.at,
        JSON.stringify(event.source),
        JSON.stringify(event.detail ?? {}),
        JSON.stringify(event.payload ?? {}),
      ]
    );

    const seq = Number(result.rows[0].seq);

    this.auditLog.push({
      seq,
      at: event.source.at,
      operation: event.operation,
      entityId: event.entityId,
      detail: event.detail,
    });

    this.nextSeq = Math.max(this.nextSeq, seq + 1);
    return seq;
  }

  async upsertEntity(input: EntityInput): Promise<EntityRecord> {
    await this.ready();

    if (
      !input ||
      typeof input.type !== "string" ||
      !input.type.trim()
    ) {
      throw new Error("Entity type is required");
    }

    const source = this.requireSource(input.source);
    const id = this.idFor(input);
    const existing = this.entities.get(id);

    const next: EntityRecord = {
      id,
      type: input.type.trim().toLowerCase(),
      name: input.name,
      externalKey: input.externalKey
        ? structuredClone(input.externalKey)
        : undefined,
      attrs: structuredClone(input.attrs ?? {}),
      source,
      updatedAt: source.at,
    };

    if (
      existing &&
      stableStringify(existing) === stableStringify(next)
    ) {
      return structuredClone(existing);
    }

    if (this.storage === "memory") {
      this.entities.set(id, next);
      this.record(
        existing ? "entity.updated" : "entity.created",
        id,
        { namespace: this.namespace },
        source.at
      );
      return structuredClone(next);
    }

    const pool = this.requirePool();
    const client = await pool.connect();
    let seq: number;

    try {
      await client.query("BEGIN");

      seq = await this.appendEvent(client, {
        operation: existing ? "entity.updated" : "entity.created",
        entityId: id,
        source,
        detail: { namespace: this.namespace },
        payload: { entity: next },
      });

      const externalSystem = next.externalKey?.system ?? null;
      const externalId = next.externalKey?.id ?? null;

      await client.query(
        `INSERT INTO public.wm_entities
           (namespace, entity_id, entity_type, name, external_system,
            external_id, attrs_json, source_json, updated_at, last_event_seq)
         VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10)
         ON CONFLICT (namespace, entity_id) DO UPDATE SET
           entity_type = EXCLUDED.entity_type,
           name = EXCLUDED.name,
           external_system = EXCLUDED.external_system,
           external_id = EXCLUDED.external_id,
           attrs_json = EXCLUDED.attrs_json,
           source_json = EXCLUDED.source_json,
           updated_at = EXCLUDED.updated_at,
           last_event_seq = EXCLUDED.last_event_seq`,
        [
          this.namespace,
          id,
          next.type,
          next.name ?? null,
          externalSystem,
          externalId,
          JSON.stringify(next.attrs),
          JSON.stringify(source),
          next.updatedAt,
          seq,
        ]
      );

      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      // Remove the local audit record added before a failed commit.
      this.auditLog.length = Math.max(0, this.auditLog.length - 1);
      throw error;
    } finally {
      client.release();
    }

    this.entities.set(id, next);
    return structuredClone(next);
  }

  async relate(input: RelationshipInput): Promise<EdgeRecord> {
    await this.ready();

    const source = this.requireSource(input?.source);

    if (
      !input.from?.trim() ||
      !input.to?.trim() ||
      !input.type?.trim()
    ) {
      throw new Error(
        "Relationship endpoints and type are required"
      );
    }

    if (!this.entities.has(input.from)) {
      throw new Error(
        `Unknown relationship source entity: ${input.from}`
      );
    }

    if (!this.entities.has(input.to)) {
      throw new Error(
        `Unknown relationship target entity: ${input.to}`
      );
    }

    if (!Number.isFinite(Date.parse(input.validFrom))) {
      throw new Error("validFrom must be a valid timestamp");
    }

    const id = `${input.from}|${input.type}|${input.to}|${input.validFrom}`;
    const existingExact = this.edges.get(id);

    if (existingExact) {
      return structuredClone(existingExact);
    }

    const oldEdges = [...this.edges.values()].filter(
      (edge) =>
        edge.from === input.from &&
        edge.type === input.type &&
        edge.validTo === undefined
    );

    for (const old of oldEdges) {
      if (Date.parse(input.validFrom) < Date.parse(old.validFrom)) {
        throw new Error(
          "Cannot supersede an edge with an earlier validFrom timestamp"
        );
      }

      if (
        old.to === input.to &&
        old.validFrom === input.validFrom
      ) {
        return structuredClone(old);
      }
    }

    const edge: EdgeRecord = {
      ...structuredClone(input),
      source,
      id,
      recordedAt: source.at,
    };

    if (this.storage === "memory") {
      for (const old of oldEdges) {
        this.edges.set(old.id, {
          ...old,
          validTo: input.validFrom,
        });
        this.record(
          "relationship.closed",
          old.id,
          { validTo: input.validFrom },
          source.at
        );
      }

      this.edges.set(id, edge);
      this.record(
        "relationship.created",
        id,
        { from: edge.from, type: edge.type, to: edge.to },
        source.at
      );

      return structuredClone(edge);
    }

    const client = await this.requirePool().connect();
    const pendingAudit: AuditRecord[] = [];

    try {
      await client.query("BEGIN");

      for (const old of oldEdges) {
        const seq = await this.appendEvent(client, {
          operation: "relationship.closed",
          entityId: old.id,
          source,
          detail: { validTo: input.validFrom },
          payload: { relationshipId: old.id },
        });

        pendingAudit.push(this.auditLog[this.auditLog.length - 1]);

        await client.query(
          `UPDATE public.wm_relationships
           SET valid_to = $1, last_event_seq = $2
           WHERE namespace = $3 AND relationship_id = $4`,
          [input.validFrom, seq, this.namespace, old.id]
        );
      }

      const seq = await this.appendEvent(client, {
        operation: "relationship.created",
        entityId: id,
        source,
        detail: {
          from: edge.from,
          type: edge.type,
          to: edge.to,
        },
        payload: { relationship: edge },
      });

      pendingAudit.push(this.auditLog[this.auditLog.length - 1]);

      await client.query(
        `INSERT INTO public.wm_relationships
           (namespace, relationship_id, from_entity_id,
            relationship_type, to_entity_id, valid_from, valid_to,
            source_json, recorded_at, last_event_seq)
         VALUES ($1, $2, $3, $4, $5, $6, NULL, $7::jsonb, $8, $9)`,
        [
          this.namespace,
          id,
          edge.from,
          edge.type,
          edge.to,
          edge.validFrom,
          JSON.stringify(source),
          edge.recordedAt,
          seq,
        ]
      );

      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      this.auditLog.splice(
        Math.max(0, this.auditLog.length - pendingAudit.length - 1),
        pendingAudit.length + 1
      );
      throw error;
    } finally {
      client.release();
    }

    for (const old of oldEdges) {
      this.edges.set(old.id, {
        ...old,
        validTo: input.validFrom,
      });
    }

    this.edges.set(id, edge);
    return structuredClone(edge);
  }

  async ingest(
    batch: Observation[]
  ): Promise<{ changed: boolean; inserted: number; updated: number }> {
    await this.ready();

    if (!Array.isArray(batch)) {
      throw new Error("Ingest batch must be an array");
    }

    let inserted = 0;
    let updated = 0;
    let changed = false;

    // Validate the complete batch before mutating state.
    for (const observation of batch) {
      if (
        !observation ||
        typeof observation.externalId !== "string" ||
        !observation.externalId.trim()
      ) {
        throw new Error(
          "Every observation requires an externalId"
        );
      }

      this.requireSource(observation.source);

      if (
        typeof observation.at !== "string" ||
        !Number.isFinite(Date.parse(observation.at))
      ) {
        throw new Error(
          `Observation ${observation.externalId} has an invalid timestamp`
        );
      }

      if (typeof observation.title !== "string") {
        throw new Error(
          `Observation ${observation.externalId} requires a title`
        );
      }
    }

    for (const observation of batch) {
      const id = observation.externalId;
      const next = structuredClone(observation);
      const previous = this.observations.get(id);

      if (
        previous &&
        stableStringify(previous) === stableStringify(next)
      ) {
        continue;
      }

      const operation = previous
        ? "observation.updated"
        : "observation.ingested";

      if (this.storage === "memory") {
        this.observations.set(id, next);
        this.record(
          operation,
          id,
          { externalId: id },
          observation.source.at
        );
      } else {
        const client = await this.requirePool().connect();

        try {
          await client.query("BEGIN");

          const seq = await this.appendEvent(client, {
            operation,
            entityId: id,
            source: observation.source,
            detail: { externalId: id },
            payload: { observation: next },
          });

          await client.query(
            `INSERT INTO public.wm_observations
               (namespace, external_id, title, observed_at,
                source_json, payload_json, last_event_seq)
             VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
             ON CONFLICT (namespace, external_id) DO UPDATE SET
               title = EXCLUDED.title,
               observed_at = EXCLUDED.observed_at,
               source_json = EXCLUDED.source_json,
               payload_json = EXCLUDED.payload_json,
               last_event_seq = EXCLUDED.last_event_seq`,
            [
              this.namespace,
              id,
              next.title,
              next.at,
              JSON.stringify(next.source),
              JSON.stringify(next),
              seq,
            ]
          );

          await client.query("COMMIT");
        } catch (error) {
          await client.query("ROLLBACK");
          this.auditLog.pop();
          throw error;
        } finally {
          client.release();
        }

        this.observations.set(id, next);
      }

      changed = true;
      if (previous) updated++;
      else inserted++;

      for (const entity of observation.entities ?? []) {
        await this.upsertEntity({
          type: entity.type,
          name: entity.name,
          attrs: {},
          source: observation.source,
        });
      }
    }

    return { changed, inserted, updated };
  }

  stateShapeCanonical(): string {
    this.assertReadyForSyncRead();

    const shape = {
      namespace: this.namespace,
      entities: [...this.entities.values()].sort((a, b) =>
        a.id.localeCompare(b.id)
      ),
      relationships: [...this.edges.values()].sort((a, b) =>
        a.id.localeCompare(b.id)
      ),
      observations: [...this.observations.entries()].sort(([a], [b]) =>
        a.localeCompare(b)
      ),
    };

    return stableStringify(shape);
  }

  view(
    kind: string
  ): {
    neighborsOf: (
      id: string,
      options?: { via?: string; at?: string }
    ) => EntityRecord[];
  } {
    this.assertReadyForSyncRead();

    if (kind !== "graph") {
      throw new Error(`Unsupported view: ${kind}`);
    }

    return {
      neighborsOf: (
        id: string,
        options: { via?: string; at?: string } = {}
      ) => {
        const atMs =
          options.at === undefined ? undefined : Date.parse(options.at);

        if (
          atMs !== undefined &&
          !Number.isFinite(atMs)
        ) {
          throw new Error(
            "Invalid point-in-time query timestamp"
          );
        }

        const matching = [...this.edges.values()].filter((edge) => {
          const validFrom = Date.parse(edge.validFrom);
          const validTo =
            edge.validTo === undefined
              ? Number.POSITIVE_INFINITY
              : Date.parse(edge.validTo);

          const validAtRequestedTime =
            atMs === undefined
              ? edge.validTo === undefined
              : validFrom <= atMs && atMs < validTo;

          return (
            edge.from === id &&
            (options.via === undefined || edge.type === options.via) &&
            validAtRequestedTime
          );
        });

        const seen = new Set<string>();
        const result: EntityRecord[] = [];

        for (const edge of matching) {
          const target = this.entities.get(edge.to);

          if (target && !seen.has(target.id)) {
            seen.add(target.id);
            result.push(structuredClone(target));
          }
        }

        return result;
      },
    };
  }

  audit(): AuditRecord[] {
    this.assertReadyForSyncRead();
    return structuredClone(this.auditLog);
  }

  private assertReadyForSyncRead(): void {
    if (!this.initialized) {
      throw new Error(
        "PostgreSQL state has not been loaded. Call and await world.ready() first."
      );
    }
  }

  async close(): Promise<void> {
    if (this.ownsPool && this.pool) {
      await this.pool.end();
    }
  }
}
