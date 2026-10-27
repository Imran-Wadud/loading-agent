# Loading Agent — isolated Meterless World Model prototype

This folder is separate from `app/engines/world_model.py`. It implements the API and behavioural invariants exercised by the Meterless `engines/world-model/conformance/runner.ts` you supplied:

- stable IDs and NFKD/name normalization
- merge-band boundary decisions
- provenance-required entity writes
- idempotent observation ingestion and changed-input detection
- bitemporal relationship supersession and point-in-time graph queries
- ordered audit entries

## Run against the official Meterless conformance runner (Windows PowerShell)

From your cloned Meterless repository, set the path to this file (adjust the path if you save it elsewhere):

```powershell
$env:WORLD_MODEL_IMPL = (Resolve-Path "F:\ImranWadud\dev\Hackathon\loading-agent\meterless_world_model\index.ts").Path
cd path\to\Meterless\engines\world-model\conformance
npx tsx runner.ts
```

The runner expects TypeScript and `tsx`; install the dependencies as described by the Meterless repository if needed.

## Scope and limitation

This is a conformance-focused prototype for the exact runner cases above, not yet a production replacement for the current Python workload simulation engine. It currently supports only `storage: "memory"`; it does not implement durable PostgreSQL/event-log storage, schema migrations, a rebuildable derived-view pipeline, or a full operator control plane. Do not claim full Meterless World Model conformance until the official runner passes against this file and the remaining requirements in the current `AGENTS.md` are implemented and verified.
