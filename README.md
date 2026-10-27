# Loading Agent — Athletic Workload & Recovery Engine

Loading Agent turns a natural-language workout description into a structured tissue-load proposal, evaluates the proposed workload against an athlete's recent training history, and records the result with an approval or risk-warning status. When a session raises a safety flag, the agent generates an optional alternative and checks the proposed alternative against the World Model before committing the original session and its advisory status.

This is a hackathon prototype for workload analysis and demonstration. Its scores and recommendations are heuristic and model-assisted; they are **not medical advice, a diagnosis, or a substitute for a qualified coach or clinician**.

## What it does

- Accepts a workout description, athlete ID, session date, and session RPE.
- Uses a three-pass Meterless Consensus flow to extract candidate tissue loads and canonical body-part IDs.
- Runs a World Model simulation using recent workload, tissue recovery windows, and a 7-day acute / 28-day chronic workload ratio (ACWR).
- Flags risk conditions such as an ACWR spike and assigns temporal handling for future/today, recent-past, and distant-past sessions.
- Generates an optional three-line remediation advisory and sends its candidate tissue loads through the evaluator for another World Model simulation, with a bounded retry loop.
- Stores sessions and tissue loads in PostgreSQL and emits structured agent/audit events to the console.
- Persists workload assessments through the Meterless World Model HTTP service and reads the resulting observation back through Zetaris USL.
- Queries a unified context from three registered sources: the training-plan reference CSV, prior athlete sessions, and the matching World Model observation.

## Architecture and workflow

```text
Workout input (dashboard or CLI)
            |
            v
   LangGraph workload graph
            |
      Cache lookup
        /      \
     hit        miss
      |           |
      |     3-pass Consensus (Nightshift/model adapter)
      |           |
      +-----------+
            |
            v
     World Model simulation
       |             |
    approved       flagged
       |             |
       |       remediation suggestion
       |             |
       |       Evaluator parses candidate tissue loads
       |             |
       |       World Model re-validation (bounded retries)
       +-------------+
            |
            v
   Commit original session and status
            |
            v
   Cascade audit of downstream sessions
```

The evaluator does not silently turn an unsafe original submission into an approved workout. The submitted session's status follows the original audit; a remediation is an optional alternative and must separately pass validation.

### Main components

| Component | Responsibility |
|---|---|
| `app/graph.py` | LangGraph state, orchestration, bounded remediation/evaluator loop, commit and cascade audit. |
| `app/engines/consensus.py` | Three-pass extraction and aggregation of candidate tissue loads. |
| `app/engines/world_model.py` | Workload simulation, temporal tiers, ACWR and tissue-load safety flags. |
| `app/adapters/zetaris.py` | Read-only data access through the Zetaris REST SQL endpoint and registered USL tables; PostgreSQL writes for application-owned session records. |
| `app/adapters/meterless.py` | OpenAI-compatible client for Meterless Nightshift, with configurable model/provider preference and fallback. |
| `app/adapters/meterless_world_model.py` | HTTP bridge that posts completed workload assessments to the Meterless World Model service. |
| `app/tracing.py` | Structured `TRACE_EVENT` and `AUDIT_SUMMARY` logging. |
| `app/ui/dashboard.py` | Reflex dashboard entry point. |
| `cli.py` | Interactive terminal entry point for running the workload graph without the dashboard. |

## Sponsor technology

### Zetaris

Zetaris is the query layer for the registered application, World Model and training-plan sources. Application reads use the USL namespace configured in `app/adapters/zetaris.py`:

`lightning.metastore.loading_agent.loading_agent_usl`

The unified training-context query joins:

- `loading_agent_training_plan_sample` — the reference training-plan rows registered from the Zetaris FileStore source;
- `athlete_sessions` — prior sessions for the athlete;
- `wm_observations` — persisted Meterless World Model assessments, matched by athlete and session date.

The graph logs this result as `USL_UNIFIED_CONTEXT`. A successful live run on 27 October 2026 returned one plan row and matched one World Model observation for `athlete_001` on `2026-10-30`. The six-row reference-plan quality check also returned six valid and zero invalid records in the configured Zetaris environment.

### Meterless World Model

After `WorldModelEngine.simulate_state_delta()` evaluates a workload, the Python adapter posts the assessment to the Meterless World Model service at `/assessments`. That service owns the World Model persistence path and stores observations/entities/events in its PostgreSQL database. Those records are then exposed as registered Zetaris USL tables so the workload graph can retrieve them as part of the unified context.

The service's default local URL is `http://127.0.0.1:8090`; its health endpoint is `/health`. The service currently lives in a separate Meterless World Model project directory rather than being launched by the Loading Agent process itself.

### Meterless Nightshift and NVIDIA Nemotron

The application uses an OpenAI-compatible client pointed at Meterless Nightshift. The configured Nemotron model is controlled by `NEMOTRON_MODEL` (the current example value is `nvidia/nemotron-3.5-lightning-30b-a3b`). With `USE_GEMINI_PRIMARY=false`, the adapter prefers the Nightshift/Nemotron client and can fall back to Gemini if a Gemini API key is configured. With `USE_GEMINI_PRIMARY=true` and a Gemini key present, Gemini is preferred for development/testing and Nightshift becomes the fallback.

The exact provider/model that handled a particular request depends on this configuration and fallback behavior. For a submission claiming that a particular request used Nemotron, keep evidence from the corresponding Nightshift/model logs rather than relying solely on the configured default.

## Requirements

- Python 3.11 recommended for the pinned Python dependencies.
- PostgreSQL for the application's session/tissue-load data.
- A running Zetaris instance with the required sources registered and the `loading_agent_usl` namespace activated.
- Meterless Nightshift running and configured with the provider/model you intend to use.
- The Meterless World Model TypeScript service and its PostgreSQL database, for persistent assessment observations.
- Node.js/npm for the separate TypeScript World Model service.

The app itself is currently run directly from a Python environment. The current repository's complete Docker build and a network-independent `SAMPLE_MODE=true` path have not been verified; do not assume either works until tested.

## Setup

### 1. Clone and install Python dependencies

From the repository root:

```powershell
git clone <your-public-repository-url>
cd loading-agent
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS/Linux, create and activate the virtual environment with `python3 -m venv .venv` and `source .venv/bin/activate` instead.

### 2. Configure environment variables

Edit `.env` locally. Never commit it. Use real values only in your local environment.

| Variable | Purpose |
|---|---|
| `ZETARIS_REST_URL` | Zetaris REST SQL base URL; local default is `http://localhost:8888/api/v1.0`. |
| `ZETARIS_ORG_ID` | Zetaris organisation ID. |
| `ZETARIS_API_KEY` | Zetaris API credential. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection for application-owned data. The adapter also supports `POSTGRES_USER` and `POSTGRES_PASSWORD` as fallbacks for the username/password. |
| `METERLESS_BASE_URL` | Nightshift OpenAI-compatible endpoint; local default is `http://127.0.0.1:8088/v1`. |
| `METERLESS_RUNTIME_TOKEN` | Runtime token issued by Meterless Nightshift. |
| `NEMOTRON_MODEL` | Model ID to request through Nightshift. |
| `USE_GEMINI_PRIMARY` | Set to `false` to prefer Nightshift/Nemotron; set to `true` to prefer Gemini when a Gemini API key is configured. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Optional Gemini fallback/development provider settings. |
| `METERLESS_WORLD_MODEL_URL` | World Model service base URL; defaults to `http://127.0.0.1:8090`. |
| `METERLESS_WORLD_MODEL_ENABLED` | Enables/disables persistence through the World Model bridge; defaults to enabled. |
| `METERLESS_WORLD_MODEL_TIMEOUT_SECONDS` | HTTP bridge timeout; defaults to `2.5`. |
| `METERLESS_WORLD_MODEL_REQUIRED` | If `true`, a failed World Model persistence request raises an error; defaults to `false` during development. |

The NVIDIA API key belongs in the Meterless Nightshift provider configuration, **not** in Loading Agent's `.env` file.

### 3. Set up the databases

Create the application database and apply its schema using the repository's schema file. For example, with PostgreSQL command-line tools:

```powershell
createdb -U <postgres-user> loading_agent_db
psql -U <postgres-user> -d loading_agent_db -v ON_ERROR_STOP=1 -f .\data\schema.sql
```

Create the separate database used by the Meterless World Model service and apply its schema:

```powershell
createdb -U <postgres-user> loading_agent_world_model
psql -U <postgres-user> -d loading_agent_world_model -v ON_ERROR_STOP=1 -f .\data\schema_postgres.sql
```

Adjust credentials, host and port for your PostgreSQL setup. The World Model service's own `.env` must point its `DATABASE_URL` (or supported World Model database settings) to `loading_agent_world_model`. Do not point the service at the application database.

### 4. Configure Zetaris

Register the appropriate PostgreSQL sources in Zetaris and expose the tables via USL in the namespace above. The working setup uses these logical sources:

| Source | Main tables/purpose |
|---|---|
| `loading_agent_db` | `athletes`, `body_parts`, `tissue_kinetics`, `athlete_sessions`, `tissue_loads_logged`. |
| `loading_agent_world_model` | Meterless World Model tables such as `wm_entities`, `wm_events`, `wm_observations`, `wm_relationships`, `wm_pipeline_checkpoints` and `wm_schema_migrations`. |
| `loading_agent_files` | `loading_agent_training_plan_sample`, registered from the reference CSV in Zetaris FileStore. |

Activate the USL definition after registering/updating source tables. The corresponding source data and credentials are environment-specific and are not bundled as credentials in this repository.

### 5. Start Meterless World Model

In the separate Meterless World Model TypeScript project directory, configure its `.env` with the PostgreSQL connection described above, install its npm dependencies if not already installed, and start the HTTP service:

```powershell
npm install
npm run serve
```

Check that `http://127.0.0.1:8090/health` returns an `ok` response and identifies PostgreSQL storage. Keep this service running while exercising the live workload flow if assessment persistence is enabled.

## Run the application

### Dashboard

With the virtual environment active and all required live services running, start the Reflex dashboard from the repository root:

```powershell
reflex run
```

### Interactive CLI

Alternatively, run the agent from the terminal:

```powershell
python cli.py --athlete athlete_001 --date 2026-10-30 --rpe 8.0
```

Enter a workout description when prompted. Example inputs:

1. `Easy cycle for 20 minutes at RPE 3`
2. `Barbell squats 2 sets of 5 at 100 kg, RPE 8`
3. `Single-leg hamstring curls 1 set of 12 at 18 kg, RPE 7`

Type `exit` to leave the interactive CLI. The resulting status and risk flags depend on the athlete's saved history, date, the consensus extraction and the configured model. Re-running the same text can yield a different result if the underlying history or model response changes.

## Logs and verification

Useful runtime evidence appears in standard output, including:

- `TRACE_EVENT` — agent step, action and details;
- `AUDIT_SUMMARY` — audit verdict, number of active flags and ACWR;
- `Meterless World Model persisted assessment` — successful persisted assessment and observation ID;
- `Zetaris USL unified read` and `USL_UNIFIED_CONTEXT` — counts and returned plan/history/assessment context;
- `EvaluatorAgent` / `remediation_validation` — whether a candidate alternative passed the second simulation.

To run the existing World Model integration test, ensure the live database and Zetaris configuration is available, then run:

```powershell
python -m pip install pytest
python -m pytest tests\test_world_model.py -s
```

This is an integration test, not a fully offline unit test. It executes the simulation against configured data and can persist a World Model assessment.

## Data and safety notes

- The training-plan CSV is a reference/demo fixture, not a clinical data source. Keep real personal, health or identifiable athlete data out of the public repository and demo artefacts.
- ACWR and tissue stress values are workload heuristics, not a clinically validated injury prediction.
- The original session remains risk-flagged when the first audit rejects it, even if an advisory alternative is generated. The candidate is not approved unless the evaluator's simulation passes.
- Current model output formatting can vary by provider. Treat remediation as an optional advisory and review it; do not present it as medical advice.

## AI, data, and third-party usage disclosure

- **Agent framework:** LangGraph orchestrates the graph; the dashboard uses Reflex and the project also provides a terminal CLI.
- **Model provider:** Meterless Nightshift through an OpenAI-compatible adapter; the configured model and Gemini fallback are controlled by environment variables.
- **Meterless Engine:** Meterless World Model persists workload assessments and exposes their observations to the agent through Zetaris USL.
- **Data layer:** Zetaris serves registered application, reference-plan and World Model sources to the agent.
- **Data:** the configured training-plan fixture is demo/reference data. Production use requires a data review and appropriate permissions.
- **AI-assisted development:** ChatGPT was used during development/documentation. Update this section if additional AI coding tools, libraries, templates or adapted code should be disclosed.
- **Known limitation:** an offline `SAMPLE_MODE=true` run, a clean-clone deployment, and a full Docker build have not been verified as part of the current implementation. These should be checked before submission; do not claim they work unless tested.

## Before submission

- [ ] Make the GitHub repository public.
- [ ] Confirm `.env` and real credentials are ignored and not staged; use placeholders only in `.env.example`.
- [ ] Add at least three recorded input/output examples (the live result is data/model-dependent, so label examples accurately).
- [ ] Keep a passing Meterless World Model test/conformance check and point to it in the final submission.
- [ ] Demonstrate a live run with logs showing Zetaris queries, World Model persistence, and the actual model that handled a request.
- [ ] Test from a fresh clone and verify the documented commands.
- [ ] Implement and test a no-service `SAMPLE_MODE=true` path, if not already implemented.
- [ ] Complete a 1–4 minute demo video, slide deck and sponsor technology explanation covering Zetaris, Meterless/Nightshift and NVIDIA/Nemotron.
- [ ] Declare pre-existing components in the hackathon submission form.
