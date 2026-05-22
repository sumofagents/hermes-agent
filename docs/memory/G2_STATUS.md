# G2 Phase 2 status — enforced first-turn semantic recall

## Built in this implementation branch

- Added a deterministic, pure G2 recall gate in `agent/recall_gate.py` for mandatory high-risk classes:
  - job/application prompts,
  - continuity/reuse prompts,
  - durable personal/profile prompts,
  - fleet/project continuity prompts with context cues.
- Added a synchronous, read-only Chroma recall path behind `memory.first_turn_recall_enabled`:
  - searches memories, session history, and the active agent collection for mandatory recall;
  - includes team collections only for fleet/project continuity;
  - deduplicates normalized duplicate candidates;
  - filters ephemeral candidates except for fleet/project status cases;
  - renders an ephemeral `## Enforced Memory Recall` block under the 3,500 character budget.
- Wired G2 through `MemoryProvider.enforced_recall()`, `MemoryManager.enforced_recall()`, and `run_agent.py` before the first LLM call for the turn.
- Preserved prompt-cache boundaries: the recall block is injected into the current API user-message copy only, not the stable system prompt and not the persisted raw message list.
- Added G1B ledger lifecycle events for `recall_needed`, `recall_retrieved`, `recall_used`, and `recall_skipped`, with `context_sha256` and structured labels instead of raw user-message storage.
- Added fail-open degraded recall notices for mandatory classes when Chroma/embedding recall is unavailable.
- Added the rollback flag:
  - default: `memory.first_turn_recall_enabled: true`
  - rollback: `hermes config set memory.first_turn_recall_enabled false`, then restart the local MacBook Rilo session/process.
- Added focused tests and a read-only/fake-fallback manifest smoke:
  - `tests/agent/test_recall_gate.py`
  - `tests/plugins/memory/test_chromadb_g2_recall.py`
  - `tests/run_agent/test_g2_recall_wiring.py`
  - `tests/plugins/memory/g2_live_manifest_smoke.py`

## Deferred exactly as contracted

- Goal 3: broad task routing, LLM-generated retrieval queries, dynamic retrieval strategies, and unified routing across memory/session/web/file/tool recall.
- Later hygiene: stale-fact cleanup, duplicate-cluster review queues, Chroma deletion/supersession workflows, dashboard visualization, and access-counter-driven salience tuning.
- No G1A salience retuning, no G1B access-counter expansion, no Chroma schema mutation, no Chroma writes, no Sentinel/Forge service restart, no deployment to other Hermes instances.

## Controller-review decisions / watch items

- Latency remains the primary live-smoke watch item. The implementation batches embeddings and wraps the embed call with a 4s timeout to respect the contract's 5s pivot budget.
- Deterministic trigger precision is intentionally conservative and covered by false-positive tests for generic software applications and generic Hermes logs.
- The enabled provider emits `recall_skipped:not_triggered` for no-recall turns when evaluated, matching the contract's ledger semantics. The kill switch disables all G2 evaluation and therefore emits no G2 recall events.
