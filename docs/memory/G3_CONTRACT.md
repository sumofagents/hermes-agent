# G3 contract — unified retrieval routing

## Goal

Goal 3 adds a deterministic unified retrieval planner for current-turn context routing. It preserves the G2 high-risk semantic recall guarantee while making route decisions explicit and testable for memory, session, web, file, and tool recall classes.

This first implementation is the dual-lane-converged Phase A: pure planner, route descriptors, privacy guards, LLM-refinement merge guard, config kill switch, and bounded rendering. It does not enable autonomous web/file/tool execution before a later privacy/latency review.

## Scope guard

In scope:

- MacBook Rilo only.
- Repository: `/Users/jeremiah/.hermes/hermes-agent/`.
- Runtime config path: `/Users/jeremiah/.hermes/config.yaml` if local enablement is needed.
- Read-only ChromaDB/Sentinel and no-mutation Forge embedding usage inherited from G2.

Out of scope:

- ChromaDB writes, schema changes, service restarts, deletions, supersession, or cleanup.
- Forge writes, model deployment, or service restarts.
- Writes to `MEMORY.md` or `USER.md`.
- Changes to `memory_tool` write semantics or builtin mirror behavior.
- Default-on web/file/tool execution.
- Deployment to any other Hermes instance.

## Architecture decision

G3 is a planner/router layer, not a replacement for G2.

- `agent.retrieval_router` owns pure deterministic route planning, route descriptors, LLM-refinement merge safety, and bounded context rendering.
- `agent.recall_gate` remains the source of truth for mandatory high-risk memory recall classes.
- G2 `MemoryManager.enforced_recall()` and provider implementations remain the live memory execution path for Phase A.
- Route descriptors cover `memory_semantic`, `session_semantic`, `session_fts`, `web_search`, `web_extract`, `file_search`, `file_read`, and `tool_recall`.
- Web/file/tool route execution is explicitly deferred; descriptors are allowed only so later executors can be contract-checked against deterministic safety decisions.

## Deterministic vs LLM boundary

Deterministic code owns all safety-critical decisions:

- whether mandatory recall is required;
- which route kinds are allowed;
- private/public query classification;
- web exclusion for private/profile/job/continuity prompts;
- file route read-only scope;
- budgets and fail-open behavior.

An LLM planner/refinement may only rewrite or reorder queries for route kinds already present in the deterministic plan. It cannot add route kinds, raise budgets, lower mandatory recall, enable web for private prompts, or request writes. Invalid refinements are ignored.

Default: `memory.retrieval_routing.llm_planner_enabled: false`.

## Config and rollback

Defaults:

```yaml
memory:
  retrieval_routing_enabled: true
  retrieval_routing:
    llm_planner_enabled: false
    char_budget: 3500
    latency_budget_ms: 5000
```

Rollback:

```bash
hermes config set memory.retrieval_routing_enabled false
```

This returns the runtime to the pre-G3 G2 recall path. Full G2 rollback remains:

```bash
hermes config set memory.first_turn_recall_enabled false
```

## Expected behavior assertions

Tests must prove:

1. Anduril/SpaceX application reuse plans mandatory memory and session routes and does not plan web.
2. Continuity-only prompts plan mandatory memory and session routes.
3. Current/public prompts can plan public web search without private memory routes.
4. Explicit URL prompts can plan web extraction.
5. Explicit repo/file prompts can plan read-only local file descriptors.
6. Arithmetic/generic prompts plan no routes.
7. Private/profile/job prompts never plan web even when the message contains freshness words.
8. LLM refinement cannot introduce unknown/disallowed routes or raise budgets.
9. Bounded rendering deduplicates repeated content and respects the budget.
10. Config defaults expose `memory.retrieval_routing_enabled` and keep LLM planning off by default.
11. Runtime initialization reads the G3 config without changing G2 semantics.

## Non-claims / deferred

- No default-on web/file/tool execution.
- No dynamic salience retuning from G1B feedback yet.
- No dashboard, stale-fact cleanup, Chroma writes, or schema changes.
- No guarantee that a planned descriptor was executed unless a later executor receipt says so.

## Pivot conditions

Re-open the contract if:

- Route planning cannot remain pure and deterministic.
- Web/file/tool execution is required in the same PR.
- Privacy review finds private facts can leak into public web queries.
- Runtime latency exceeds the G2 5s budget once executors are added.
- Implementation requires Chroma writes, schema changes, remote service restarts, or memory write semantics changes.

## Dual-lane reconciliation

Codex GPT-5.5 approved a broader full router plan. Claude Opus 4.7 approved only a safer Phase A and objected to shipping web/file/tool execution in the first patch due to privacy and latency risk. This contract adopts the converged subset: route descriptors and guards now, execution later under a separate gate.
