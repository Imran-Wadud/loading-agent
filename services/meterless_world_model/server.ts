import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { WorldModel } from "./index.ts";

type JsonObject = Record<string, unknown>;
type AssessmentRequest = {
  runId?: unknown;
  at?: unknown;
  athlete?: { id?: unknown; name?: unknown };
  session?: { id?: unknown; name?: unknown; date?: unknown };
  assessment?: unknown;
};

const host = process.env.METERLESS_WM_HOST ?? "127.0.0.1";
const port = Number(process.env.METERLESS_WM_PORT ?? 8090);
const namespace = process.env.WORLD_MODEL_NAMESPACE ?? "loading-agent";
const world = new WorldModel({ storage: "postgres", namespace });

function send(res: ServerResponse, status: number, body: JsonObject): void {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(JSON.stringify(body));
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > 1_000_000) throw new Error("Request body exceeds 1 MB");
    chunks.push(buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} must be a non-empty string`);
  }
  return value.trim();
}

function asObject(value: unknown, field: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${field} must be a JSON object`);
  }
  return value as JsonObject;
}

const server = createServer(async (req, res) => {
  const route = new URL(req.url ?? "/", `http://${host}:${port}`).pathname;

  if (req.method === "GET" && route === "/health") {
    send(res, 200, { ok: true, engine: "meterless-world-model", storage: "postgres", namespace });
    return;
  }

  if (req.method !== "POST" || route !== "/assessments") {
    send(res, 404, { ok: false, error: "Not found" });
    return;
  }

  try {
    const body = await readJson(req) as AssessmentRequest;
    const athlete = asObject(body.athlete, "athlete");
    const session = asObject(body.session, "session");
    const assessment = asObject(body.assessment, "assessment");

    const athleteId = requiredString(athlete.id, "athlete.id");
    const athleteName = requiredString(athlete.name, "athlete.name");
    const sessionId = requiredString(session.id, "session.id");
    const sessionName = typeof session.name === "string" && session.name.trim()
      ? session.name.trim()
      : `Session ${sessionId}`;
    const runId = typeof body.runId === "string" && body.runId.trim()
      ? body.runId.trim()
      : `${sessionId}:${Date.now()}`;
    const at = typeof body.at === "string" ? body.at : new Date().toISOString();
    if (!Number.isFinite(Date.parse(at))) throw new Error("at must be a valid ISO-compatible timestamp");
    const sessionDate = typeof session.date === "string" ? session.date : null;
    const source = { kind: "loading-agent-workload-simulation", at, runId };

    const athleteEntity = await world.upsertEntity({
      type: "athlete",
      name: athleteName,
      externalKey: { system: "loading-agent", id: athleteId },
      attrs: {
        latest_workload_assessment: assessment,
        latest_session_id: sessionId,
        latest_session_date: sessionDate,
        assessed_at: at,
      },
      source,
    });

    const sessionEntity = await world.upsertEntity({
      type: "training_session",
      name: sessionName,
      externalKey: { system: "loading-agent", id: sessionId },
      attrs: {
        athlete_id: athleteId,
        session_date: sessionDate,
        workload_assessment: assessment,
        assessed_at: at,
      },
      source,
    });

    const observationExternalId = `loading-agent-assessment:${runId}`;
    await world.ingest([{
      externalId: observationExternalId,
      title: `Workload simulation: ${athleteName} / ${sessionName}`,
      at,
      source,
      athlete_id: athleteId,
      athlete_entity_id: athleteEntity.id,
      session_id: sessionId,
      session_entity_id: sessionEntity.id,
      session_date: sessionDate,
      assessment,
    }]);

    send(res, 200, {
      ok: true,
      persisted: true,
      engine: "meterless-world-model",
      storage: "postgres",
      namespace,
      run_id: runId,
      athlete_entity_id: athleteEntity.id,
      session_entity_id: sessionEntity.id,
      observation_external_id: observationExternalId,
      audit_event_count: world.audit().length,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid request";
    const clientError = /must be|exceeds 1 MB|timestamp|JSON object|Unexpected end|JSON at position/i.test(message);
    console.error("[meterless-world-model] assessment request failed:", message);
    send(res, clientError ? 400 : 500, {
      ok: false,
      error: clientError ? message : "World Model persistence failed; check the service logs.",
    });
  }
});

async function main(): Promise<void> {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("METERLESS_WM_PORT must be a valid TCP port");
  }
  await world.ready();
  server.listen(port, host, () => {
    console.log(`[meterless-world-model] listening on http://${host}:${port} namespace=${namespace} storage=postgres`);
  });
}

async function shutdown(): Promise<void> {
  server.close(() => {
    void world.close().finally(() => process.exit(0));
  });
}

process.on("SIGINT", () => { void shutdown(); });
process.on("SIGTERM", () => { void shutdown(); });

main().catch((error) => {
  console.error("[meterless-world-model] startup failed:", error instanceof Error ? error.message : String(error));
  void world.close().finally(() => process.exit(1));
});
