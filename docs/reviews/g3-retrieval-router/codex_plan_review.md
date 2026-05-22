No files edited.

**Goal 3 Plan**

1. **Architecture / Seams**
- Add a core planner module, likely `agent/retrieval_router.py`, owning pure route planning, route allowlists, complexity classification, query budgets, and JSON validation.
- Keep [agent/recall_gate.py](/Users/jeremiah/.hermes/hermes-agent/agent/recall_gate.py:62) as the deterministic G2 high-risk memory gate; G3 may call it but should not weaken it.
- [run_agent.py](/Users/jeremiah/.hermes/hermes-agent/run_agent.py:12713) remains the only turn orchestration seam. If G3 is enabled, run one unified pre-LLM retrieval plan before first API call; if disabled, keep the current G2 path bit-identical.
- Memory routes go through [agent/memory_manager.py](/Users/jeremiah/.hermes/hermes-agent/agent/memory_manager.py:333) and provider hooks. Web/file/session routes should not be owned by `MemoryManager`; use a new runtime executor module so memory does not become a generic tool router.
- Chroma provider remains read-only for G3, refactoring [plugins/memory/chromadb/__init__.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/__init__.py:691) into reusable route execution while preserving `enforced_recall()` compatibility.
- Injection continues via ephemeral current-user-message context only, using [append_ephemeral_context_to_user_message](/Users/jeremiah/.hermes/hermes-agent/agent/recall_gate.py:94). No system prompt mutation and no session DB persistence.

2. **Config / Rollback**
- Add `memory.retrieval_router_enabled: true` as the G3 kill switch.
- Add `memory.retrieval_router.llm_planner_enabled: true|false` as a narrower rollback for LLM-generated queries. I would start default `false` or `shadow` until live smoke proves value.
- Optional tuning under `memory.retrieval_router`: `max_routes_per_turn`, `total_char_budget`, per-route timeouts, route enables for `chroma`, `session_fts`, `file`, `web`, `tool`.
- Existing `memory.first_turn_recall_enabled` must still disable mandatory G2/Chroma memory enforcement. Full rollback: set `memory.retrieval_router_enabled false`; pre-G3 G2 behavior resumes.

3. **Deterministic vs LLM Boundary**
- Deterministic owns safety-critical decisions: high-risk memory triggers, privacy class, web eligibility, file path scope, route allowlist, freshness/currentness detection, URL/path extraction, and no-route decisions.
- LLM planner may only generate/rewrite retrieval queries and rank already-allowed routes for complex/ambiguous prompts.
- LLM output must be strict JSON, schema-validated, timeout-bounded, and unable to add disallowed routes, lower mandatory memory, request writes, or send private memory/profile facts to web.
- Invalid/timeout LLM planning falls back to deterministic plan.

4. **Route Types / Safety**
- `memory_semantic`: Chroma memories, user/profile, active agent, team collections where class allows. Read-only only.
- `session_fts`: SQLite FTS via `SessionDB.search_messages()` / `session_search(mode="fast")`; exclude current session lineage.
- `session_semantic`: Chroma session summaries for fuzzy continuity.
- `file_search` / `file_read`: read-only, cwd/repo/explicit-path scoped, deny secrets by default (`.env`, key files, private config) unless explicitly requested.
- `web_search` / `web_extract`: only for current/public/external facts or explicit URLs. Sanitize queries; never include recalled private facts in web queries.
- `tool_recall`: idempotent read-only tools only, using the existing allowlist pattern from [agent/tool_guardrails.py](/Users/jeremiah/.hermes/hermes-agent/agent/tool_guardrails.py:13).

5. **Expected Tests**
- Anduril/SpaceX prompt routes mandatory `memory_semantic` plus session recall, no web.
- `same as before` triggers mandatory memory/session recall.
- “latest/current” public query routes web only.
- URL prompt routes `web_extract`.
- “in this repo/file” routes file search/read only.
- Arithmetic/generic coding prompt routes none.
- LLM planner cannot enable web for private/profile prompts or introduce unknown routes.
- G3 off produces identical first API payload to pre-G3 path.
- Route failures fail open, emit `recall_skipped`, and do not block the turn.
- Injected route context is not persisted to session DB and ledger stores hashes/labels, not raw user text.
- Chroma/Sentinel/Forge remain read-only; no `MEMORY.md` or `USER.md` writes.

6. **Pivot Conditions**
- P95 pre-LLM retrieval latency exceeds 5s.
- LLM planner invalid/timeout rate is materially high, e.g. above 5-10%.
- Any private prompt content leaks into web/file/tool queries.
- Route false positives cause noisy context on simple tasks.
- Implementation requires Chroma schema changes, Chroma writes, service restarts, or memory_tool semantic changes.
- Multi-route context regularly exceeds budget or degrades answer quality.

7. **Likely Files**
- New: `docs/memory/G3_CONTRACT.md`, `agent/retrieval_router.py`, `agent/retrieval_context.py`, maybe `agent/retrieval_executor.py`.
- Edit: [run_agent.py](/Users/jeremiah/.hermes/hermes-agent/run_agent.py:12713), [hermes_cli/config.py](/Users/jeremiah/.hermes/hermes-agent/hermes_cli/config.py:1163), [agent/memory_provider.py](/Users/jeremiah/.hermes/hermes-agent/agent/memory_provider.py:106), [agent/memory_manager.py](/Users/jeremiah/.hermes/hermes-agent/agent/memory_manager.py:333), [plugins/memory/chromadb/__init__.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/__init__.py:691), [plugins/memory/chromadb/g2_recall.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g2_recall.py:1), [plugins/memory/chromadb/g1b_observability.py](/Users/jeremiah/.hermes/hermes-agent/plugins/memory/chromadb/g1b_observability.py:26).
- Tests: `tests/agent/test_retrieval_router.py`, `tests/run_agent/test_g3_retrieval_wiring.py`, `tests/plugins/memory/test_chromadb_g3_routes.py`.

8. **Risks / Disagreements**
- Do not put web/file routing into `MemoryManager`; that would blur ownership.
- LLM planning is the riskiest part. It should start behind a separate flag or shadow mode.
- Automatic web routing has real privacy risk; deterministic redaction and route eligibility must be strict.
- G2 and G3 must not double-inject memory context. G3 enabled should replace the G2 orchestration path while preserving G2 semantics inside the plan.

Verdict: APPROVED_PLAN.