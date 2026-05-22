The plan is fully written to `/Users/jeremiah/.claude/plans/you-are-an-independent-ethereal-sundae.md`. Here is the concise Goal 3 plan as requested.

# Goal 3 Plan — Unified Memory Retrieval Routing

**Resolved defaults** (clarifying questions went unanswered — alternatives in §8): deterministic core with LLM refinement opt-in/off; memory routes implemented, web/file/tool specified-and-deferred to a Phase B amendment.

### 1. Architecture & ownership seams
A **planner + router** layered *on top of* G2 (not replacing it). New pure module `agent/retrieval_planner.py` (`plan_retrieval`, `merge_llm_refinement`, `RetrievalPlan/Route`, `ComplexityTier`) — same purity contract as `recall_gate.py`. New `agent/retrieval_router.py` executes a plan under one deadline. Optional `agent/retrieval_llm_classifier.py`. Provider routes in `plugins/memory/chromadb/g3_routes.py`. Seam: `run_agent.py:12710-12769` wraps the existing `enforced_recall` call; `enforced_recall` stays untouched as the Chroma route → memory-route parity guaranteed. G3 lives *inside* the G2 gate.

### 2. Config / rollback
`memory.retrieval_routing_enabled` (master kill switch), `memory.retrieval_llm_classifier_enabled` (off by default), `memory.retrieval_routing.{allowed_routes,latency_budget_ms,char_budget}`. Rollback: `hermes config set memory.retrieval_routing_enabled false` → bit-identical to G2; `first_turn_recall_enabled false` → pre-G2.

### 3. Deterministic vs LLM boundary
Deterministic layer owns **every safety decision**: whether to recall (G2 `classify_risk`), permitted route *types*, all budgets/deadlines, filtering, fail-open. LLM may *only* lower the tier, reorder routes, and add queries for already-permitted routes — validated by pure `merge_llm_refinement`; any rejection/timeout/parse-fail → deterministic plan. LLM never gates, never enables a route, never raises a budget.

### 4. Route types & safety
Phase A: `chroma_memory`, `chroma_session`, `chroma_team`, `session_fts` (local). Phase B (deferred, privacy review): `web`, `file`, `tool`. Common rules: read-only Sentinel/Forge, fail-open, one global deadline, cross-route dedup, 3,500-char budget, `context_sha256`-only ledger. Hard rule: personal/profile/job/continuity classes never route to `web`.

### 5. Assertions/tests
15 numbered assertions (TDD RED→GREEN): routing-off bit-identical to G2; G3 inside G2 gate; Anduril route parity; default `planner_mode=deterministic`; LLM-fail → deterministic fallback; merge rejects route/budget escalation; tier mapping; web/file/tool never planned by default; cross-route dedup; deadline + degraded notice; planner purity guard; no MEMORY/USER/Chroma writes. New test files mirror G2 layout + live/fake manifest smoke.

### 6. Pivot conditions
G2 seam changes; no local FTS session store exists; planner can't stay pure; >5s latency; merge-safety unprovable; any route needs Chroma writes/restarts; web/file/tool demanded default-on pre-review.

### 7. Files to edit
**New:** `agent/retrieval_planner.py`, `agent/retrieval_router.py`, `agent/retrieval_llm_classifier.py`, `plugins/memory/chromadb/g3_routes.py`, `docs/memory/G3_CONTRACT.md`+`G3_STATUS.md`, tests. **Edit (additive):** `run_agent.py:12710-12769`, `hermes_cli/config.py:~1158`, `agent/memory_manager.py`, `g1b_observability.py` (`ALLOWED_EVENT_TYPES`). **Reused unchanged:** `recall_gate.py`, `g2_recall.py`, `enforced_recall`.

### 8. Risks/disagreements
(1) **Scope** — disagree with shipping web/file/tool in one effort; they cross the read-only-memory boundary → phase them. (2) **LLM classifier** — extra pre-LLM call adds latency/cost/failure mode → keep off by default. (3) Bit-identical rollback harder with a planner layer → short-circuit before any planner construction. (4) Latency: G2 already ~4.8s, fan-out + LLM call risks the 5s budget. (5) **"session FTS"** premise — G2's `search_sessions` is semantic Chroma, not local FTS; must confirm a real local FTS store exists. (6) `merge_llm_refinement` determinism is the whole safety argument. (7) Web route PII leakage to third parties — class-exclusion rule mandatory before Phase B.

---

## Verdict: **APPROVED_PLAN**

For **Phase A** as scoped — deterministic unified planner/router over memory/session routes, LLM refinement opt-in/off, strictly additive over G2, fail-open, single-flag rollback. Conditioned on: controller confirmation of D1/D2; Phase 1 being a docs-only `G3_CONTRACT.md` PR under dual-lane review (matching G1A/G2 sequencing); web/file/tool handled by a separate Phase B amendment after privacy review.

Changes to **REQUEST_CHANGES_PLAN** only if the controller requires all five route types and/or LLM-first-default in a single Goal 3 effort — that needs the latency and privacy reviews named in §8 resolved first.
