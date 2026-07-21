# G1C contract — local memory doctor and Chroma readiness

## Goal

After G1A boot synthesis, G1B observability, G2 enforced recall, and G3 Phase A retrieval routing have landed, add a narrow Goal 1 follow-on that makes the local MacBook Rilo memory stack inspectable and safely probeable.

G1C exposes a read-only `hermes memory doctor` command that summarizes local boot/feedback receipts, validates effective memory config, and optionally verifies the existing ChromaDB and Forge embedding endpoints without writing to remote services.

## Scope guard

In scope:

- MacBook Rilo checkout only: `/Users/jeremiah/.hermes/hermes-agent/`.
- Local config/readiness reporting for `/Users/jeremiah/.hermes/config.yaml` and `/Users/jeremiah/.hermes/chromadb.json`.
- Local append-only logs read-only: `~/.hermes/logs/boot_synthesis.jsonl` and `~/.hermes/logs/memory_feedback.jsonl`.
- Optional read-only probes against the already-configured endpoints:
  - ChromaDB on Sentinel: `100.107.68.104:8000`.
  - Forge embedding service: `100.113.1.2:8006`.

Out of scope:

- Chroma writes, upserts, deletes, schema changes, collection creation, or data cleanup.
- Forge model changes or service restarts.
- Any deployment to non-Mac Hermes instances.
- Changing `MEMORY.md`, `USER.md`, memory_tool semantics, G1A scoring, G2 recall semantics, or G3 route planning.
- Making `hermes memory status` perform network I/O.

## Design

Add a new subcommand:

```bash
hermes memory doctor
hermes memory doctor --json
hermes memory doctor --limit 200
hermes memory doctor --probe
hermes memory doctor --probe --strict
```

Default behavior is local-only and fast. It must not open network sockets. It reads only:

- effective Hermes config via `load_config()`;
- `chromadb.json` parse status;
- boot synthesis receipts through the G1B reader;
- memory feedback events through the G1B reader.

`--probe` opts in to network checks. Probes are read-only:

- Chroma: construct a client, call `heartbeat()`, then `get_collection(...).count()` for configured collections. Never call `get_or_create_collection()`.
- If the optional `chromadb` Python package is unavailable in the CLI environment, the Chroma probe may fall back to Chroma v2 HTTP endpoints for heartbeat, collection listing, and collection-id counts. The fallback must remain read-only.
- Forge: call `GET /health` and `POST /embed` with `{"texts": ["Hermes Chroma readiness probe"]}`. Never call `/embed-single` because the current Forge contract is `/health` plus `/embed`.

`--strict` exits nonzero only when an executed check fails. Without `--probe`, skipped network checks are not failures.

## Expected behavior assertions

1. `hermes memory doctor` summarizes boot and feedback ledgers without mutating either file.
2. Default doctor does not probe Chroma or Forge.
3. `hermes memory doctor --json` includes boot receipt and feedback event summaries, including G2 `recall_*` event counts.
4. Effective config validation treats absent G1A/G2/G3 keys according to `DEFAULT_CONFIG`; raw YAML omissions must not create false negatives for default-on features.
5. `--probe` reports Chroma heartbeat and configured collection counts using read-only collection access.
6. `--probe` reports Forge health and single-text embed dimensions using `/health` and `/embed`, not `/embed-single`.
7. `--strict` raises `SystemExit(1)` for failed executed checks and returns normally when all executed checks pass.
8. Missing or malformed local JSONL files are reported in summaries without creating or repairing files.

## Rollback

No runtime behavior changes occur unless the user invokes the new diagnostic command. Rollback is removing the command/code or ignoring it. No ChromaDB or Forge rollback is required.
