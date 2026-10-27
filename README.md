# Loading Agent — Athletic Workload & Recovery Engine

**Hackathon track:** Reasoning Architecture  
**Interface:** Reflex dashboard + interactive CLI  
**Core stack:** Python, LangGraph, PostgreSQL, Zetaris USL, Meterless Nightshift, Meterless World Model, NVIDIA Nemotron

Loading Agent helps athletes and coaches evaluate a proposed training session against recorded workload and recovery context. It converts a workout description into a structured tissue-load proposal, simulates the candidate workload, records risk flags and can send a suggested alternative back through an evaluator and another simulation.

> **Prototype and safety notice:** Loading Agent uses heuristic and model-assisted workload estimates. ACWR and tissue-stress checks are not clinically validated injury predictions or medical advice. Outputs should be reviewed by a qualified coach or clinician.

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Setup on Windows](#setup-on-windows)
- [Configure Zetaris](#configure-zetaris)
- [Run the application](#run-the-application)
- [Verify the end-to-end integration](#verify-the-end-to-end-integration)
- [Troubleshooting](#troubleshooting)
- [Demo video script](#demo-video-script)
- [Limitations and next steps](#limitations-and-next-steps)
- [Sponsor technology](#sponsor-technology)

## What it does

1. **Extracts a workout.** A three-pass consensus flow turns natural-language input into a candidate tissue-load profile with canonical body-part identifiers.
2. **Simulates workload.** The Python World Model evaluates the candidate using the available athlete history, 7-day acute and 28-day chronic workload, ACWR and tissue-recovery rules.
3. **Audits and traces the outcome.** The graph records an audit verdict, risk flags and structured events such as `TRACE_EVENT` and `AUDIT_SUMMARY`.
4. **Checks an alternative.** When a proposal is flagged, a remediation step may suggest a lower-load alternative. The candidate must be parsed and evaluated separately; a model suggestion is not automatically considered safe. The retry loop is bounded.
5. **Persists and retrieves evidence.** The Python engine sends a completed workload assessment to the separate Meterless World Model HTTP service. Its persisted observation can be exposed through Zetaris USL and queried alongside the plan and previous-session history.

A session outcome depends on stored athlete history, selected session date, the current code/configuration and model output. Repeating a workout after storing new sessions can change the context and result.

## Architecture

```text
User: workout description, athlete, date, RPE
                 |
                 v
       Reflex dashboard / CLI
                 |
                 v
        Python + LangGraph
                 |
        Three-pass consensus
                 |
                 v
       Python World Model
       - recent workload / ACWR
       - recovery windows
       - safety flags
          |             |
      no risk         risk flag
          |             |
          |       remediation suggestion
          |             |
          |       evaluator + re-simulation
          |        (bounded retries)
          +-------------+
                 |
                 v
        Commit status + trace
                 |
                 +------> Python adapter --HTTP--> Meterless World Model
                 |                                  (TypeScript service)
                 |                                          |
                 |                                          v
                 |                                      PostgreSQL
                 |
                 +------> Zetaris REST SQL / USL <--- registered sources
                           |      |      |
                           |      |      +-- World Model observations
                           |      +--------- prior athlete sessions
                           +---------------- training-plan reference rows
                 |
                 v
        Unified audit context / logs
```

The TypeScript service is **a separate process**. It persists assessment observations through the Meterless World Model implementation; it does not replace `app/engines/world_model.py`, which still computes the workload assessment and its approval/risk outcome.

### Key components

| Path                                                 | Role                                                                                                                                                                                               |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run.py`                                             | Launches the Reflex app using the project's Reflex configuration.                                                                                                                                  |
| `rxconfig.py`, `app/app.py`                          | Reflex configuration and app entry point.                                                                                                                                                          |
| `app/graph.py`                                       | LangGraph state, orchestration, audit flow, remediation/evaluator loop and commit/cascade operations.                                                                                              |
| `app/engines/consensus.py`                           | Multi-pass workout-to-tissue-load extraction.                                                                                                                                                      |
| `app/engines/world_model.py`                         | Workload calculation, ACWR, tissue recovery and risk flags.                                                                                                                                        |
| `app/adapters/meterless.py`                          | OpenAI-compatible client for Meterless Nightshift and the configured model/provider preference.                                                                                                    |
| `app/adapters/meterless_world_model.py`              | HTTP client for persisting a completed assessment to the separate TypeScript service.                                                                                                              |
| `app/adapters/zetaris.py`                            | PostgreSQL access for app-owned records and read queries through Zetaris REST SQL/USL.                                                                                                             |
| `services/meterless-world-model/server.ts`           | HTTP server exposing `/health` and `POST /assessments`.                                                                                                                                            |
| `services/meterless-world-model/index.ts`            | Meterless World Model implementation used by the service.                                                                                                                                          |
| `services/meterless-world-model/schema_postgres.sql` | PostgreSQL schema for World Model persistence (if this is the path in your current checkout).                                                                                                      |
| `data/schema.sql`                                    | Application tables and initial schema. **This schema contains** `DROP TABLE IF EXISTS` **statements: use it only when setting up a fresh/empty app database.**                                     |
| `scripts/seed_db.py`                                 | Applies `data/schema.sql` and seeds the default athlete/body-part dictionary. This also runs the destructive schema statements; do not run it against a database containing data you need to keep. |

## Repository layout

The main app and the Meterless service have separate runtimes and configuration files. The relevant structure should resemble:

```text
loading-agent/
├── app/
│   ├── adapters/
│   │   ├── meterless.py
│   │   ├── meterless_world_model.py
│   │   └── zetaris.py
│   ├── engines/
│   │   ├── consensus.py
│   │   └── world_model.py
│   ├── graph.py
│   └── ui/
│       └── dashboard.py
├── data/
│   └── schema.sql
├── services/
│   └── meterless-world-model/
│       ├── server.ts
│       ├── index.ts
│       ├── package.json
│       ├── package-lock.json
│       ├── schema_postgres.sql
│       └── .env.example
├── .env.example
├── .gitignore
├── requirements.txt
├── rxconfig.py
├── run.py
└── README.md
```

If a filename is different in your committed checkout, use the actual path shown by `git status`/Explorer. Commit the service source, `package.json`, `package-lock.json`, schema and environment **examples**, but not real `.env` files or `node_modules`.

## Prerequisites

For the documented Windows/PowerShell workflow:

- **Python 3.11** (recommended for the pinned requirements).
- **Node.js 22 or newer** and npm for the TypeScript World Model service.
- A running PostgreSQL server reachable from the host. Your app and the World Model use separate databases; they can live on one PostgreSQL server.
- A Zetaris environment with REST SQL access, the expected source connections, and the `loading_agent_usl` USL namespace registered/activated.
- A Meterless Nightshift runtime URL and runtime token, with the intended model provider configured in Nightshift.
- Git to clone the repository.

A fresh clone does **not** automatically include your local database contents, Zetaris source registrations/activation, Nightshift provider credentials, or any data registered outside Git. Those items must be available/configured separately on the other machine.

## Setup on Windows

### 1. Clone the repository

Open PowerShell in the directory where you keep development projects:

```powershell
git clone <YOUR_PUBLIC_GITHUB_URL>
cd loading-agent
```

Replace the URL and folder name with the actual public repository values.

### 2. Create the Python environment

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, use the activation instructions appropriate for your machine or run the virtual environment's Python directly. Do not change machine-wide execution policy just for this project unless you understand the implications.

Open the root `.env` in a local editor and fill in the actual values. Do not commit or share this file.

#### Root `.env` settings

| Variable | Purpose / what to check |
|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection for Loading Agent application-owned tables. `DB_NAME` must be the database where `data/schema.sql` was applied. Use your currently working value rather than assuming the database is named `postgres`. |
| `ZETARIS_REST_URL` | Base URL for Zetaris REST SQL, commonly `http://localhost:8888/api/v1.0` in the existing local setup. Use the real reachable endpoint. |
| `ZETARIS_ORG_ID`, `ZETARIS_API_KEY` | Zetaris org ID and REST API credentials. Never paste the API key into the README, issues or screenshots. |
| `METERLESS_BASE_URL` | Meterless Nightshift OpenAI-compatible endpoint, commonly `http://127.0.0.1:8088/v1` for the local setup. |
| `METERLESS_RUNTIME_TOKEN` | Runtime token issued by Nightshift; this is not the NVIDIA provider API key. |
| `NEMOTRON_MODEL` | Model ID requested by the app through Nightshift; current example: `nvidia/nemotron-3.5-lightning-30b-a3b`. Verify the resolved model in Nightshift/run evidence if claiming a specific request used it. |
| `USE_GEMINI_PRIMARY` | Set to `false` to prefer Nightshift/Nemotron for this build. Set to `true` only when intentionally preferring the direct Gemini provider and a Gemini key is configured. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Optional direct Gemini fallback/dev settings. Keep the key blank if not used. |
| `METERLESS_WORLD_MODEL_ENABLED` | Enables/disables the Python-to-World-Model persistence request. |
| `METERLESS_WORLD_MODEL_URL` | HTTP service URL, normally `http://127.0.0.1:8090` when both processes run on the same host. |
| `METERLESS_WORLD_MODEL_TIMEOUT_SECONDS` | HTTP request timeout. |
| `METERLESS_WORLD_MODEL_REQUIRED` | When true, a failed persistence request should fail the workflow rather than silently passing. Keep true for end-to-end verification. |

For this build, the NVIDIA provider credential belongs in **Meterless Nightshift's provider settings**, not in the repository or the app's `.env`. Leave placeholders in `.env.example` only.

### 3. Configure the application PostgreSQL database

Use the existing app database if it already contains the required tables and data. On a fresh machine, first create an empty app database and set root `.env` `DB_NAME`/credentials to match it.

If you use the helper, understand the warning first:

```powershell
python scripts/seed_db.py
```

`seed_db.py` applies `data/schema.sql`, and that schema includes `DROP TABLE IF EXISTS` for the application tables before recreating them. **Only run this against a fresh/empty application database or after backing up data and deliberately intending to reset those tables. Never run it as a routine startup command on the working database.**

If your database already has application data, do not run the seed script; point the app at that database and preserve its contents.

### 4. Configure the Meterless World Model service

The TypeScript server must be running before an end-to-end assessment can be persisted. It is a separate process from the Reflex UI.

Open a **second PowerShell terminal** at the service directory:

```powershell
cd services\meterless-world-model
Copy-Item .env.example .env
```

Configure the service `.env` to connect to its **own** PostgreSQL database, `loading_agent_world_model` in the example setup. The inspected `index.ts` reads `WORLD_MODEL_DATABASE_URL`, or the `WORLD_MODEL_PG`* / `POSTGRES_*` host, port, user, password and database variables. It does **not** read a generic `DATABASE_URL` by itself.

Recommended connection-string form:

```dotenv
WORLD_MODEL_DATABASE_URL=postgresql://zetaris:URL_ENCODED_PASSWORD@127.0.0.1:5432/loading_agent_world_model
METERLESS_WM_HOST=127.0.0.1
METERLESS_WM_PORT=8090
WORLD_MODEL_NAMESPACE=loading-agent
```

Use URL-encoded credentials if the password contains characters such as `@`, `:`, `/`, `#` or `%`. Alternatively, use these individual connection settings instead of the connection string:

```dotenv
WORLD_MODEL_PGHOST=127.0.0.1
WORLD_MODEL_PGPORT=5432
WORLD_MODEL_PGUSER=zetaris
WORLD_MODEL_PGPASSWORD=replace-with-your-postgres-password
WORLD_MODEL_PGDATABASE=loading_agent_world_model
```

Do not commit the real credentials. The service's database is distinct from the database selected by the root `.env` `DB_NAME`.

#### Create the World Model database/schema (fresh setup only)

If the database does not already exist, create it with a user allowed to create databases:

```powershell
createdb -h 127.0.0.1 -p 5432 -U zetaris loading_agent_world_model
```

Apply the World Model schema from the service directory, adjusting the path if your committed tree differs:

```powershell
psql -h 127.0.0.1 -p 5432 -U zetaris -d loading_agent_world_model -v ON_ERROR_STOP=1 -f .\schema_postgres.sql
```

Skip these initialization commands if that database/schema already exists. Do not drop or recreate a populated World Model database just to set up another machine; arrange a backup/restore or a fresh database as appropriate.

If PostgreSQL is running in Docker but the TypeScript server runs on Windows, confirm the PostgreSQL port is published to the host. For your local container name, a check such as this can help:

```powershell
docker port zetaris-postgres-1 5432
```

If that prints no mapping, a host-side Node process cannot reach the container through `127.0.0.1:5432`. Use the actual published port or run the process in the same Docker network; do not guess a container's host port.

#### Install and start the Node service

Still inside `services\meterless-world-model`:

```powershell
npm ci
npm run serve
```

`npm ci` uses the committed `package-lock.json`. If the current service `package.json` does not define a `serve` script, ensure the intended script is present (for example `"serve": "node --env-file=.env --import tsx server.ts"`) and commit the updated `package.json` and lockfile. Leave this terminal running.

Check its health endpoint from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8090/health
```

A healthy response should contain `ok: true`, `engine: "meterless-world-model"`, `storage: "postgres"` and the namespace. The server also exposes `POST /assessments`, called by the Python adapter during a workload run.

### 5. Configure Zetaris USL

The application queries Zetaris REST SQL with the USL namespace:

```text
lightning.metastore.loading_agent.loading_agent_usl
```

Make sure Zetaris can access and expose the registered sources used by the unified-context query:

| Logical source | Data used by Loading Agent |
|---|---|
| `loading_agent_db` | Application tables such as `athletes`, `body_parts`, `tissue_kinetics`, `athlete_sessions`, and `tissue_loads_logged`. |
| `loading_agent_world_model` | Meterless World Model persistence tables, including `wm_entities`, `wm_events`, `wm_observations`, `wm_relationships`, and checkpoints/migrations. |
| `loading_agent_files` | Reference training-plan data exposed as `loading_agent_training_plan_sample` through Zetaris FileStore. |

The USL model/SQL assets in the repository are configuration examples for your environment; credentials and activation state are not supplied by Git. Register the correct source connections and activate/update the USL objects in your Zetaris workspace.

**Important:** `USL_UNIFIED_CONTEXT: []` means the unified query returned no rows. It does not, by itself, prove the World Model failed to persist. The training-plan join is date- and athlete-dependent; check that a plan exists for the exact athlete/date, check the registered table and source, and separately verify the `Meterless World Model persisted assessment` log and observation visible through Zetaris.

### 6. Start the Reflex dashboard

Open a **third PowerShell terminal** at the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

`run.py` should launch Reflex using `python -m reflex run`. Open the local URL printed by Reflex. Keep the World Model terminal open and ensure PostgreSQL, Zetaris and Nightshift are reachable.

An optional terminal entry point is available:

```powershell
python cli.py --athlete athlete_001 --date 2026-10-30 --rpe 8.5
```

The CLI prompts for a workout description. Use the athlete/date/RPE that matches the data available in your environment. The exact result is state-dependent and may differ between runs.

## Verify the end-to-end workflow

Check these in order:

1. **Database connection:** the app's PostgreSQL connection uses the database where `data/schema.sql` is installed and populated. The Node service connects to `loading_agent_world_model`.
2. **World Model server:** `Invoke-RestMethod http://127.0.0.1:8090/health` returns `ok: true`.
3. **Dashboard:** `python run.py` opens Reflex.
4. **Persistence:** a workload simulation produces a `Meterless World Model persisted assessment` log and an observation/run ID.
5. **Zetaris:** the logs show `Zetaris USL unified read` and `USL_UNIFIED_CONTEXT`. Check `plan_rows` and the actual returned rows.
6. **Audit and remediation:** inspect `AUDIT_SUMMARY`, reason codes and evaluator/remediation logs; don't treat a generated suggestion as approved unless its evaluation passes.

Useful log markers:

- `TRACE_EVENT`: structured agent/workflow events.
- `AUDIT_SUMMARY`: verdict, active flag count and reported ACWR.
- `Meterless World Model persisted assessment`: acknowledgement from the HTTP persistence service.
- `Zetaris USL unified read` and `USL_UNIFIED_CONTEXT`: unified query counts/results.
- evaluator/remediation validation logs: whether the alternative passed its own simulation.

Re-running an audit for the same date can add another session to stored history, changing later audit context. Use a clean demo database or carefully prepared test data if you need repeatable recordings; avoid repeatedly submitting a scenario against the same live database without understanding how its history changes.

## Troubleshooting

| Symptom | Things to check |
|---|---|
| `python run.py` says Reflex is missing | Activate `.venv` and run `python -m pip install -r requirements.txt`. |
| `npm run serve` is missing | Check `services/meterless-world-model/package.json` contains a `serve` script; commit `package.json` and `package-lock.json`. |
| Port 8090 isn't reachable | Check the Node terminal for startup errors, `METERLESS_WM_HOST`/`METERLESS_WM_PORT`, and whether another process owns the port. |
| Node server fails to connect to PostgreSQL | Check `WORLD_MODEL_DATABASE_URL` or the individual `WORLD_MODEL_PG*`/`POSTGRES_*` variables, database existence, schema and host-published port. The generic `DATABASE_URL` alone is not read by the inspected source. |
| Python run fails when persistence is required | Check the World Model service is running and the root `.env` URL matches its reachable host. `METERLESS_WORLD_MODEL_REQUIRED=true` intentionally makes a failed persistence request fail the run. |
| Zetaris REST query fails | Check `ZETARIS_REST_URL`, `ZETARIS_ORG_ID`, `ZETARIS_API_KEY`, network reachability and registered/activated USL objects. |
| `USL_UNIFIED_CONTEXT: []` | Check athlete/date, plan rows for that date, USL table mapping and source activation. Verify persistence independently; `[]` does not automatically mean the Node write failed. |
| Nightshift/model call fails | Check `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN`, the model ID requested, and the configured provider's availability in Nightshift. NVIDIA provider keys are managed in Nightshift. |
| Works locally but not on a second PC | Compare both `.env` files, Python/Node versions, PostgreSQL databases and data, Zetaris registrations, Nightshift provider settings and reachable URLs. These are environment-specific and are not cloned by Git. |
| Docker networking behaves differently | Inside a container, `127.0.0.1` refers to that container. The current repository does not include a verified full Docker Compose deployment. Use reachable service names/host addresses only after configuring and testing the network. |

## Demo video script (approximately 2:50)

This script matches the recorded demo flow: first audit approval, adding a previous-day session, a new risk-warning state, and accepting a remediation. **The displayed ACWR remains** `0.67` **when the risk-warning state appears in that recording, so do not narrate that as an ACWR spike.** The change demonstrates that audit context/status changed after history was added; the specific rule/status consistency still needs further testing.

### 0:00–0:20 — Problem and target user

> Loading Agent helps athletes and coaches evaluate a planned workout in the context of recent training history and recovery. Instead of treating a workout in isolation, it brings together structured workload estimates, stored sessions and a World Model assessment to make the decision easier to inspect.

**On screen:** dashboard overview.

### 0:20–1:12 — First workload audit

> First, I'm evaluating a single-leg hamstring curl for athlete 001 on 30 October: 18 kilograms for one set of 12, at an RPE of 8.5. With the history currently available, the audit approves this session and displays an ACWR of 0.67. The result reflects the workload context the system has at this point in the workflow.

**On screen:** workout form and initial approved result. Keep the actual inputs/result visible.

### 1:12–1:54 — Add training history

> Next, I'll log a previous-day training session containing squats, hamstring curls and calf raises. That session is also approved. Now the athlete's stored history includes an additional session before the workout we're planning to assess.

**On screen:** enter and save the session dated 29 October. Pause on the saved result.

### 1:54–2:24 — Re-run the planned session

> Now I return to the same planned hamstring curl on 30 October and run the audit again. This time, the application proposes a risk warning, and the remediation text points to possible cumulative hamstring fatigue. Notice that the displayed ACWR remains 0.67 in this run, so I'm not claiming the warning was caused by an ACWR spike. What this demonstrates is that adding prior training history can change the audit context and outcome; explaining and testing this status transition deterministically is an important next step.

**On screen:** second result with `PROPOSED_RISK_WARNING` and the actual reason text. Show the ratio so the narration stays transparent.

### 2:24–2:50 — Accept remediation and close

> The agent can now propose a lower-load alternative. I accept the suggested active-recovery and low-load eccentric session, and the interface records `REMEDIATED_ACCEPTED`. This shows the workflow beyond initial generation: assess the original, surface a risk, and handle the proposed alternative through a separate step. Loading Agent is a prototype for workload decision support, not a clinically validated injury prediction system.

**On screen:** accepted result, `REMEDIATED_ACCEPTED`, and the final alternative title.

### Demo recording checklist

- Show the real inputs, outcomes and transition; do not fabricate or replace output values.
- If you record a new run and its ACWR/flags differ, update the narration to match the new result.
- Show the real `Meterless World Model persisted assessment` event and a non-empty `USL_UNIFIED_CONTEXT` if available. If the unified context is empty, don't present it as a successful join; explain the missing plan/history match or leave it out of the video.
- Verify and show the resolved model in Nightshift if claiming that a particular request used NVIDIA Nemotron. A configured model ID alone is not proof of which model handled a particular call.
- Redact credentials, tokens, private URLs and personal data from screen captures.
- Keep the final video between 1 and 4 minutes.

## Limitations and next steps

- The current audit rules and stress/ACWR values are prototype heuristics, not validated clinical thresholds.
- Outcome consistency depends on history, date and model-generated extraction/remediation; deterministic regression tests are still needed around baseline handling, warning versus approval status, repeat runs and malformed alternatives.
- The unified context can be empty when the date-/athlete-specific plan join finds no row even if assessment persistence succeeded.
- A fully reproducible clean-clone setup requires database schema/data and Zetaris/Nightshift environment configuration. The current setup guide does not claim an offline `SAMPLE_MODE` or full Docker Compose deployment has been verified.
- Future work: deterministic audit tests; clearer provenance from each flag to input and source row; explicit resolved-model logging; deployment automation for app + World Model + PostgreSQL; and a tested sample/offline path.

## Sponsor technology

- **Zetaris:** the app's query layer for registered app data, the training-plan reference and persisted World Model tables. The `USL_UNIFIED_CONTEXT` read combines plan/history/assessment context when matching source rows are available.
- **Meterless Nightshift:** the OpenAI-compatible endpoint used by the app's model adapter for consensus/remediation-related model calls.
- **Meterless World Model:** the separate TypeScript HTTP service calls the Meterless World Model implementation to upsert athlete/session entities and persist provenance-stamped assessment observations in PostgreSQL.
- **NVIDIA Nemotron:** the app requests the configured model through Nightshift (current example: `nvidia/nemotron-3.5-lightning-30b-a3b`). The actual resolved model must be evidenced in Nightshift/run logs for any specific demo request.
- **Cursor / development tooling:** disclose the tools actually used in the build according to the hackathon form's instructions.

## Before publishing

- [ ] Confirm the repository is public and the README matches the actual committed paths.
- [ ] Commit `services/meterless-world-model/server.ts`, `index.ts`, `package.json`, `package-lock.json`, schema and `.env.example`.
- [ ] Commit the root `.env.example`; do not commit either real `.env`.
- [ ] Check `.gitignore` excludes `**/node_modules/`, `.venv/`, real env files, local databases and volume directories.
- [ ] Run `git status --short` and `git diff --check`; inspect staged files for credentials and local data.
- [ ] Verify the service starts with `npm ci` + `npm run serve` on a clean clone.
- [ ] Verify the app starts with `python -m pip install -r requirements.txt` + `python run.py`.
- [ ] Perform one end-to-end run and capture the actual persistence and Zetaris query logs.
- [ ] Ensure the demo script and track name agree with the actual final video and submission form.
