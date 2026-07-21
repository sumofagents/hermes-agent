# G2 contract: enforced first-turn semantic recall

PR title: feat(memory): G2 contract — enforced first-turn semantic recall

## Goal

Make high-risk first-turn memory reliance code-enforced instead of prompt-aspirational. When a fresh user turn depends on prior personal/profile/application/project context, Hermes must perform semantic recall before asking the user to repeat information or answering from incomplete boot context.

This document is the Goal 2 docs-only contract. It does not implement recall, mutate ChromaDB, edit config, restart services, or change runtime behavior.

## Scope guard

In scope:

- MacBook Rilo only.
- Repository: `/Users/jeremiah/.hermes/hermes-agent/`.
- Runtime config path to be referenced by the implementation PR: `/Users/jeremiah/.hermes/config.yaml`.
- Read-only access to ChromaDB on Sentinel `100.107.68.104:8000` and no-mutation inference calls to the Forge embedding service `100.113.1.2:8006`.
- Local append-only observability through the G1B feedback ledger at `$HERMES_HOME/logs/memory_feedback.jsonl`.

Out of scope and forbidden for Goal 2:

- Writes to ChromaDB, Chroma schema changes, collection cleanup, deletion, supersession, or service restart on Sentinel.
- Writes, model deployment, embedding-service changes, or service restart on Forge.
- Writes to `MEMORY.md` or `USER.md`.
- Changes to `memory_tool` write semantics or `builtin_mirror` mirroring.
- New boot-profile salience weights or dynamic weighting.
- Dashboard visualization.
- Deployment to any Hermes instance other than this MacBook Rilo checkout.
- Merge to `main` without explicit controller authorization.

Abort condition: if an implementation or review lane proposes work outside this scope, the controller must stop that lane and surface the scope violation.

## Architecture / Design

G2 is a per-turn retrieval enforcement layer, not another boot-memory artifact.

G1A made the Chroma `external_system_prompt_block` a first-class boot memory artifact with receipts. G1B added local observability and a feedback ledger with placeholder `recall_*` events. G2 consumes that foundation by adding a deterministic high-risk intent gate and a synchronous recall pass before the first LLM call for the turn.

The recalled context must be injected into the current turn's user-message context, not into the stable system prompt. This preserves Hermes prompt-cache behavior while ensuring the model sees the retrieved facts before it decides whether to ask clarifying questions.

## Non-goals

G2 does not tune G1A salience, does not perform stale-fact deletion, does not add a dashboard, does not write to ChromaDB, does not mutate curated flat-file memory, and does not implement the broad Goal 3 task-routing system. G2 may include a narrow deterministic risk detector only to decide whether first-turn recall is mandatory.

## Open Questions

None for this contract draft. Implementation may tune thresholds only through the contract amendment path if live tests show the mandatory recall gate is too broad or too narrow.

## Global Execution Rules

The controller owns git operations, branch creation, commits, PR creation, CI observation, reviewer reconciliation, and final merge decisions.

G2 follows contract-first sequencing:

1. Phase 1: docs-only contract PR, one contract file plus optional review/status custody under `docs/reviews/g2-first-turn-recall/`.
2. Phase 2: implementation PR only after the contract PR is merged.
3. Phase 3: final mixed review and stop-before-merge unless explicit controller authorization is provided.

Codex GPT-5.5 and Claude Opus 4.7 must run separate read-only lanes for plan/contract review and for final implementation review. No phase advances on single-lane approval.

## Ownership Seams

- `run_agent.py` owns turn orchestration, prompt-cache invariants, and API-call-time context injection.
- `run_agent.py:12626-12660` owns the existing `pre_llm_call` plugin hook context path.
- `run_agent.py:12697-12718` owns memory-provider turn notification and current external prefetch call.
- `run_agent.py:12882-12899` owns ephemeral context injection into the current user message.
- `agent/memory_manager.py:312-331` owns provider prefetch fanout and failure isolation.
- `plugins/memory/chromadb/__init__.py:673-714` owns existing async/next-turn prefetch behavior.
- `plugins/memory/chromadb/__init__.py:1196-1432` owns semantic search over agent, session, team, and accessible collections.
- `plugins/memory/chromadb/g1b_observability.py` owns local JSONL feedback schema and append helpers.
- `MEMORY.md` and `USER.md` remain owned by the legacy memory store and user-facing memory writes.

Goal 2 implementation must not redefine these boundaries. If it adds a new module, the preferred shape is provider-local pure helpers plus a narrow `MemoryManager`/provider method for enforced recall, with `run_agent.py` only coordinating the timing and injection.

## Decision Rules

Gate outcomes:

- Proceed from contract drafting to Phase 1 PR only when planctl reports `status=pass`, completeness is `1.0`, deficiencies are empty, and both Codex GPT-5.5 and Claude Opus 4.7 return `APPROVED` or equivalent no-blocker approval.
- Refine the contract when planctl reports missing sections, either review lane returns `REQUEST_CHANGES`, or either lane identifies ambiguous scope, untestable assertions, privacy risk, prompt-cache risk, or non-Mac side effects.
- Block Phase 2 implementation when the Phase 1 contract PR is unmerged, when either review lane is absent, when only one lane approves, or when controller authorization has not been given.
- Block merge of the implementation PR even after green CI and dual-lane approval until explicit controller authorization is provided.
- Re-open the contract instead of patching implementation when a pivot condition is triggered.

Decision authority:

- Sub-agents and external reviewers advise; they do not merge, deploy, broaden scope, restart services, or authorize phase advancement.
- The controller reconciles reviewer divergences and records the disposition in PR notes or status docs.
- Any Sentinel/Forge write, Chroma schema mutation, MEMORY.md/USER.md mutation, or deployment beyond MacBook Rilo is an automatic stop condition.

## Worktree / Branch Strategy

Phase 1 branch: `feat/memory-g2-contract`.

Phase 1 is docs-only. It may modify:

- `docs/memory/G2_CONTRACT.md` as the contract artifact.
- `docs/reviews/g2-first-turn-recall/*` for review prompts/verdicts/status custody if the controller elects to commit review receipts.

It must not modify Python runtime files, tests, config, ChromaDB, or local memory files.

The controller alone may run `git fetch`, `git pull`, `git merge`, `git rebase`, branch deletion, or PR merge commands. Implementation lanes in later phases must use separate worktrees or a single controller-owned checkout; no two write-enabled agents may mutate the same checkout concurrently.

## Controller Operations

Before editing, the controller verifies the active branch and clean working tree. During Phase 1, the controller writes the contract, mirrors it into the plan-control-plane if evaluator coverage is required, obtains Codex and Claude read-only reviews, reconciles any `REQUEST_CHANGES`, and opens a docs-only PR. The controller stops before merge unless explicit authorization is provided.

During Phase 2, the controller must use TDD for behavior changes: RED tests for risk detection, mandatory recall injection, fallback, receipts, and no raw text leakage; then GREEN implementation; then mixed re-review.

## Existing code anchors

These anchors are current as of `main` at merge commit `a596c1db81eeaf8811640186b85ccfb643b2e78d`:

- `run_agent.py:12287` — `AIAgent.run_conversation()` starts the per-turn path.
- `run_agent.py:12481-12482` — `original_user_message` is preserved before context injections.
- `run_agent.py:12517-12556` — stable system prompt is built or reused before preflight compression and before turn-context injection.
- `run_agent.py:12626-12660` — `pre_llm_call` hook can append ephemeral context to the current user message.
- `run_agent.py:12697-12718` — current memory `on_turn_start()` then `prefetch_all()` flow.
- `run_agent.py:12712-12718` — `_ext_prefetch_cache` is currently fetched once before the tool loop.
- `run_agent.py:12882-12899` — external memory and plugin context are injected into the current user message and are not persisted to session DB.
- `agent/memory_manager.py:312-331` — `prefetch_all()` collects provider prefetch context and swallows provider failures.
- `plugins/memory/chromadb/__init__.py:675-691` — current `prefetch()` returns only completed background prefetch from a previous queued turn.
- `plugins/memory/chromadb/__init__.py:693-714` — current `queue_prefetch()` warms `agent_memories` asynchronously for the next turn.
- `plugins/memory/chromadb/__init__.py:1290` — `search_sessions()` supports semantic session-summary retrieval.
- `plugins/memory/chromadb/__init__.py:1430` — `search_all_accessible()` searches accessible Chroma collections.
- `plugins/memory/chromadb/g1b_observability.py:23-30` — G1B already reserves `recall_needed`, `recall_retrieved`, `recall_used`, and `recall_skipped` event types.

If implementation discovery contradicts these anchors, halt and amend the contract rather than silently patching around drift.

## Architectural decision

The G2 artifact is an ephemeral, per-turn recall context block inserted into the current user message before the first LLM call for that turn.

The gate evaluates on every user turn before that turn's first LLM call. "First-turn" in this goal name denotes the high-value fresh-context failure class where a brand-new session lacks task-specific memory; it is not a restriction that disables G2 after turn 1. Receipts include a `first_turn` flag only to record whether the evaluated turn was the session's first user turn.

It is not:

- a new stable system prompt block,
- an overwrite of `MEMORY.md` or `USER.md`,
- a Chroma writeback path,
- a replacement for G1A boot synthesis,
- a user-visible memory tool write.

Rationale: high-risk first-turn recall depends on the actual user request. It can require many form-specific or project-specific facts that cannot fit in the 2,200-character boot block. Injecting the retrieved context into the current user message solves the Anduril-class failure while preserving prompt-cache stability.

## Trigger policy

G2 must distinguish three trigger levels:

### Mandatory recall

Semantic recall is mandatory before the first LLM call when the current user message indicates any of these classes:

- Job applications, employment forms, resumes, cover letters, recruiter replies, work authorization, clearance, sponsorship, compensation, education, demographics, essential-functions questions, or application portals.
- Reuse/continuity language: `same as before`, `use what we used before`, `as discussed`, `already discussed`, `you know this`, `why are you asking`, `don't you remember`, `continue from last time`, `resume where we left off`, `reuse`, `using what you know from`, `what you know from the`, `from the previous application`, `from the SpaceX application`, or equivalent cross-application/cross-form reuse phrasing.
- Personal/profile questions where the answer likely exists in durable memory: identity, education, employment history, location, preferences, legal/profile constraints, long-term goals, family/personal facts previously saved.
- Project/fleet continuity requests referring to Rilo/Scout/Caddie/Ledger/Librarian/Wanderer/Foundry/Atlas/Hermes work without enough local context in the current conversation.

Mandatory recall must run before the assistant can ask the user for the missing information.

### Opportunistic recall

Recall may run for medium-risk prompts that likely benefit from memory but do not require it, such as broad project planning with prior context hints. Opportunistic recall can fail open without forcing fallback text.

### No recall

Recall should not run for simple one-off tasks with no memory dependency, such as arithmetic, generic coding questions, or current facts that should use web/system/file tools instead.

## Recall query construction

For mandatory recall, implementation must build a small deterministic query set from the user message and risk class. The initial v1 contract requires at least:

1. The original user message, sanitized for control characters.
2. A compact class-specific expansion, for example:
   - job/application: `job application resume work authorization clearance sponsorship education employment history prior application answers SpaceX Anduril`.
   - continuity/reuse: `same as before previous answer prior session user preferences durable facts`.
   - fleet/project: `project status prior decisions durable conventions current agent context`.
3. A profile/durable query when the risk class implies personal facts: `durable user profile identity preferences legal constraints education employment location`.

The contract intentionally avoids an LLM-generated query in v1 so the recall gate is deterministic and testable. Later Goal 3 may add richer routing/query generation.

## Collections and result limits

Mandatory recall searches must include, at minimum:

- `agent_memories` / memories.
- `session_history` / sessions.
- The active agent collection when configured, such as `agent_rilo`.

It may include `team_knowledge` and `team_ops` for project/fleet classes, but user-profile/application recall must prioritize user/memory/session sources over team ops.

Initial result limits:

- Up to 8 memory/profile facts.
- Up to 5 session-history snippets.
- Up to 5 team/project snippets when project/fleet class is active.
- Final injected recall block budget: 3,500 characters.

The result set must include source labels, collection names, IDs, scores where available, and concise snippets. It must not include raw hidden chain-of-thought or non-user-visible internal scratchpad data.

## Selection and safety rules

Implementation must:

- Prefer durable and high-source-quality G1A/G1B-compatible metadata when available.
- Deduplicate exact normalized content hashes before injection.
- Suppress candidates classified as ephemeral unless the risk class explicitly asks for recent project status.
- Preserve source IDs in receipts so post-hoc audits can explain what was shown to the model.
- Never persist the full raw user message in new G2 receipt fields; use `context_sha256` or existing session ID references for local correlation.
- Never write selected/dropped labels back to ChromaDB.
- Fail open if ChromaDB or embeddings are unavailable: boot/turn must continue without crashing, but a `recall_skipped` event must be appended locally with the reason. For mandatory classes, fail-open must also inject a short degraded-recall notice into the current user-message context before the first LLM call. The notice must instruct the model to tell the user that stored memory could not be reached before relying on memory-dependent answers, and to ask only for the missing information rather than pretending recall succeeded.

## Injection format

The injected block must be fenced and explicit. Required shape:

```text
## Enforced Memory Recall
Reason: <risk_class>; mandatory=<true|false>
Instruction: Use this retrieved context before asking the user to repeat information. If a needed value is not present, ask only for the missing value and say what was already found.

Sources:
- [<collection>:<id>] score=<score> source=<source> target=<target> durability=<label>
  <snippet>
```

The block is appended to the current user message through the same API-call-time context path used by existing external memory prefetch. It must not be written into the stored `messages` list or session DB as part of the user's raw message.

## Observability and receipts

G2 must use the G1B local feedback ledger when the G2 gate is enabled. Each evaluated turn emits a lifecycle sequence, not a single mutually exclusive event. Successful mandatory recall may therefore emit `recall_needed`, one or more `recall_retrieved` events, and `recall_used` for the same turn.

Required event semantics:

- `recall_needed`: emitted when mandatory or opportunistic recall is triggered. Include labels for risk class, mandatory flag, first-turn flag, query count, configured collections, and recall latency fields initialized for the turn. Store hashes/snippets only as allowed by the G1B privacy rules.
- `recall_retrieved`: emitted for selected candidates injected into the user-message context. Include fact ID, collection, source, target, score, rank, query index, and retrieval latency when available.
- `recall_used`: emitted when recall context is injected into the first API call for the turn. Include final injected character count and total recall latency in milliseconds.
- `recall_skipped`: emitted when the enabled gate evaluates and recall is not triggered, Chroma/embedding is unavailable, no candidates pass filters, budget excludes all candidates, or recall fails open. Include skip reason and elapsed latency in milliseconds.

Kill-switch semantics: when `memory.first_turn_recall_enabled=false`, G2 code must not evaluate the gate, must not inject recall context, and must not append any new G2 `recall_*` events. This preserves bit-identical pre-G2 behavior for rollback.

Privacy rule: G2 receipts must not store raw user messages. Use `context_sha256` and structured labels. Candidate snippets may be omitted or hash-only in feedback events; the injected prompt block may contain snippets because it is part of the live model input, but the append-only ledger should remain privacy-minimized.

## Config and kill switch

Add a new config flag in the implementation PR:

```yaml
memory:
  first_turn_recall_enabled: true
```

Semantics:

- Default: true for the MacBook Rilo instance after implementation.
- When false: no enforced recall runs, no recall context is injected, and the system reverts to the pre-G2 external prefetch behavior.
- Rollback command:

```bash
hermes config set memory.first_turn_recall_enabled false
```

Optional implementation-only tuning keys may be added under `memory.first_turn_recall` if the implementation needs budgets or thresholds, but the default path must work without requiring user configuration beyond the top-level kill switch.

## Expected behavior assertions

Implementation PR tests must prove:

1. With `first_turn_recall_enabled=true`, a first-turn job-application prompt triggers mandatory recall before the first model call.
2. The Anduril-class prompt `help me fill this Anduril application using what you know from the SpaceX application` triggers job/application and continuity labels.
3. A continuity prompt such as `same as before` triggers mandatory recall even without job keywords.
4. A generic arithmetic or one-off coding prompt does not trigger recall.
5. When ChromaDB returns candidates, the first API call receives an `## Enforced Memory Recall` block in the current user message.
6. Injected recall context is not persisted to session DB as raw user text.
7. With ChromaDB unreachable or embeddings unavailable for a mandatory class, the turn does not crash, emits `recall_skipped` with a failure reason, and injects the degraded-recall notice before the first model call.
8. With `first_turn_recall_enabled=false`, behavior is bit-identical to the pre-G2 prefetch path for the same inputs: identical assembled API request payload and no new G2 `recall_*` ledger events.
9. G1B feedback ledger receives `recall_needed`, `recall_retrieved`, `recall_used`, or `recall_skipped` according to the outcome and never stores raw user message text.
10. Retrieval searches memory and session collections for mandatory job/application recall.
11. Dedup prevents identical normalized candidates from appearing twice in the injected block.
12. Ephemeral candidates are excluded unless the risk class explicitly asks for recent project status.
13. MEMORY.md and USER.md byte-content are unchanged across a G2 recall run.
14. The injected `## Enforced Memory Recall` block, including any degraded-recall notice, respects the 3,500-character budget.

## Worked-example reachability proof required in Phase 2

The implementation PR must include a manifest smoke test or documented local smoke that demonstrates, against live read-only ChromaDB when reachable:

- A fresh first turn with an Anduril/SpaceX application-reuse prompt triggers mandatory recall.
- The recall query searches both memories and session history.
- At least one durable USER/profile fact or prior application/session fact is selected when available.
- The injected block is under the 3,500-character budget.
- The feedback ledger contains required `recall_*` fields, including elapsed recall latency.
- No Chroma writes occur.
- `MEMORY.md` and `USER.md` hashes are unchanged.

If live ChromaDB is unavailable during testing, the implementation must provide a fake-provider manifest smoke plus a documented live-smoke deferral. Unavailability is not permission to skip unit/contract coverage.

## Pivot conditions

Re-open this contract rather than patching silently if:

- The existing `run_agent.py` API-call-time injection seam changes materially before implementation.
- The current memory provider prefetch/search APIs cannot support synchronous first-turn recall without broad refactor.
- ChromaDB or Forge embedding latency makes first-turn recall exceed a 5 second added-latency budget for mandatory classes on the Mac.
- The deterministic trigger policy catches too many unrelated prompts in tests and cannot be narrowed without an LLM classifier.
- Privacy review finds that required receipts would store raw user text or sensitive snippets beyond the G1B privacy envelope.
- The implementation would require Chroma writes, schema changes, or remote service restarts.

## Rollback

Single-line config rollback after implementation:

```bash
hermes config set memory.first_turn_recall_enabled false
```

Then restart only the local MacBook Rilo CLI session or local MacBook Rilo gateway process. No code revert, Chroma rollback, MEMORY.md rollback, USER.md rollback, Sentinel restart, Forge restart, or restart/deploy on any other Hermes host should be required.

## Deferred scope

Deferred to Goal 3:

- General task-routing and broad intent classification.
- LLM-generated retrieval queries.
- Dynamic retrieval strategies by request complexity.
- Routing between Chroma, session FTS, web, file, and tool-specific recall based on a unified planner.

Deferred to later hygiene:

- stale-fact cleanup,
- duplicate-cluster review queues,
- Chroma deletion/supersession workflows,
- dashboard visualization,
- access-counter-driven salience tuning.

## Dual-lane gate requirements

Before Phase 1 PR publication, Codex GPT-5.5 and Claude Opus 4.7 must independently review this contract and return `APPROVED` or `REQUEST_CHANGES`.

Reviewer checklist:

- The contract is docs-only.
- The architecture uses per-turn user-message context injection, not stable system prompt mutation.
- The scope is Mac-only and read-only against Sentinel/Forge.
- G2 does not mutate Chroma, MEMORY.md, USER.md, G1A salience, or `memory_tool` writes.
- The trigger policy covers the Anduril/SpaceX failure class.
- Receipts use the G1B ledger and do not store raw user messages.
- Expected behavior assertions are testable with TDD.
- Rollback is a single config flag.

No implementation phase may begin until this contract PR is merged.
