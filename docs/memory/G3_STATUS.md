# G3 status — unified retrieval routing

## Built in this implementation branch

- Added `agent/retrieval_router.py`, a pure deterministic Goal 3 planner/router helper module.
- Added route descriptors for:
  - `memory_semantic`
  - `session_semantic`
  - `session_fts`
  - `web_search`
  - `web_extract`
  - `file_search`
  - `file_read`
  - `tool_recall`
- Preserved G2 as the mandatory high-risk recall floor by reusing `agent.recall_gate.classify_risk()` and `build_queries()`.
- Added privacy guards:
  - job/application, continuity, and personal/profile classes do not plan web routes;
  - all planned routes are read-only descriptors;
  - file route descriptors are local/private and deny obvious secret-file requests at the planning layer.
- Added `merge_llm_refinement()` with deterministic authority boundaries: LLM refinement can only rewrite/reorder queries for already-allowed route kinds and cannot add web/file/tool routes or raise budgets.
- Added bounded `render_route_context()` with normalized-content deduplication.
- Added config defaults:
  - `memory.retrieval_routing_enabled: true`
  - `memory.retrieval_routing.llm_planner_enabled: false`
  - `memory.retrieval_routing.char_budget: 3500`
  - `memory.retrieval_routing.latency_budget_ms: 5000`
- Wired `run_agent.py` initialization to read and store the G3 config without changing the existing G2 execution path.

## Deferred

- Default-on web/file/tool execution.
- LLM planner calls before the first LLM response.
- Cross-tool execution receipts.
- Dynamic salience tuning from G1B access counters/correction feedback.
- Stale-fact cleanup, duplicate review queues, Chroma deletion/supersession, and dashboards.

## Safety and rollback

G3 Phase A is additive and pure. It does not write to ChromaDB, Sentinel, Forge, `MEMORY.md`, or `USER.md`.

Rollback:

```bash
hermes config set memory.retrieval_routing_enabled false
```

Full G2 recall rollback remains:

```bash
hermes config set memory.first_turn_recall_enabled false
```

## Verification

Focused G3 TDD command:

```bash
uv run --with pytest python -m pytest -o addopts='' -q tests/agent/test_retrieval_router.py
```

Initial RED:

- failed with `ModuleNotFoundError: No module named 'agent.retrieval_router'`.

GREEN:

- Initial planner/router implementation: `9 passed`.
- After final-review privacy hardening for non-G2 private freshness prompts, private/token/profile URLs, credential URL keys, URL userinfo credentials, internal/private/link-local/non-global/special-use URL targets including alternate and mixed-radix numeric loopback, multicast/site-local, and bracketed IPv6 forms, public URLs with file/path terms, secret-file path variants, FILE_READ positive coverage, private job-relationship phrases, and file-route LLM refinement: `22 passed`.

## Controller-review decisions

The dual planning lanes disagreed on scope breadth. Codex approved full unified routing. Claude approved only a Phase A subset and requested web/file/tool execution be deferred. The controller adopted the converged safe subset to preserve dual-lane concurrence rather than overriding the privacy/latency objection.
