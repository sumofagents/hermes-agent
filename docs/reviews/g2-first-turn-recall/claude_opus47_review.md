# G2 Contract Review — Independent Read-Only Lane

**Review basis:** I assessed the contract text as provided. In this session I did not have repository read access to independently verify the code anchors (`run_agent.py:12626-12660`, `g1b_observability.py:23-30`, etc.) against `main@a596c1db8`. That is not a contract-phase blocker: the contract includes an explicit drift clause ("If implementation discovery contradicts these anchors, halt and amend the contract rather than silently patching"), which is the correct discipline for anchor drift.

## Dimension findings

**Scope** — Clean and tightly bounded. The forbidden list explicitly covers every item I am mandated to ensure is *not* authorized: Chroma writes/schema changes/restart, Forge writes/restart, MEMORY.md/USER.md writes, deployment to non-Rilo instances, and implementation before contract merge (Phase 2 is gated on Phase 1 merge). Read-only Chroma + no-mutation Forge inference is correctly characterized. An abort/stop condition for scope violations is present.

**Architecture** — Matches the desired design: ephemeral per-turn user-message context injection before the first LLM call, explicitly *not* stable system-prompt mutation, with prompt-cache preservation called out. Correctly builds on G1A boot synthesis and G1B `recall_*` events. The "first-turn" naming ambiguity is resolved well (gate evaluates every turn; `first_turn` is a recorded flag, not a turn-1 restriction) and is consistent with "each high-risk user turn."

**Testability** — Strong. 14 concrete assertions, deterministic v1 trigger policy (no LLM classifier), TDD RED→GREEN required in Phase 2, and a worked-example reachability proof with a fake-provider fallback. The Anduril/SpaceX reuse class is directly covered (assertions 2, 3, plus the worked example).

**Privacy** — Well-handled. `context_sha256` instead of raw user messages, hash-only/omitted snippets in the ledger, a clear distinction between the live injected block (snippets allowed) and the append-only ledger (privacy-minimized), exclusion of chain-of-thought, and a privacy pivot condition.

**Rollback** — Single config flag with bit-identical pre-G2 semantics, backed by a testable assertion (#8: identical assembled payload, no new `recall_*` events). No code revert or remote restart required; restart scoped to the local Rilo process only.

**Gate discipline** — Contract-first sequencing, dual independent read-only lanes, planctl gate (`status=pass`, completeness `1.0`, empty deficiencies — matches the supplied result), controller-owned git/merge, stop-before-merge, and re-open-the-contract-on-pivot rules are all explicit.

The contract is internally consistent and does not authorize any prohibited action.

VERDICT: APPROVED

## Non-blocking suggestions

1. **Fail-open should cover slow-but-reachable Chroma/Forge, not just "unavailable."** The Selection-and-safety fail-open clause names only unavailability. Synchronous recall on the user's critical path needs a defined per-turn recall timeout, with timeout treated as a fail-open `recall_skipped` (and the degraded-recall notice for mandatory classes). The 5s figure currently lives only in the pivot conditions; a per-turn timeout belongs in the behavior spec. This is the most substantive item.

2. **Config namespace consistency.** `memory.first_turn_recall_enabled` (scalar) alongside `memory.first_turn_recall` (map for tuning) creates two sibling keys with near-identical names. Prefer `memory.first_turn_recall.enabled` so kill switch and tuning share one namespace.

3. **Add negative-case trigger tests for keyword false positives.** Assertion 4 only exercises arithmetic/coding. Add cases where a trigger word appears in a non-memory context (e.g., "explain how location services work," generic "compensation structure") to validate the deterministic detector against over-triggering before the pivot condition is hit.

4. **Add an explicit opportunistic-recall assertion.** Assertions 1–14 cover mandatory and no-recall but not opportunistic. A test that an opportunistic-class prompt with Chroma unavailable fails open *silently* (no degraded-recall notice) would lock in the mandatory-vs-opportunistic distinction.

5. **Make latency an observable expectation.** The worked example records elapsed recall latency; consider an explicit threshold expectation tied to the 5s pivot budget so latency regressions surface as a signal rather than only at pivot time.

6. **Note ledger growth.** With the gate enabled, every turn emits at least a `recall_skipped`. A one-line note deferring rotation/sampling of `no_recall` skips to G1B would prevent unbounded JSONL growth from being an unowned gap.

7. **Clarify the strength of "code-enforced."** G2 code-enforces *retrieval and injection*; actual *use* of recalled facts remains model-instructed. The Architecture section is mostly honest about this ("ensuring the model sees the retrieved facts"), but the Goal sentence's "code-enforced" phrasing reads stronger. A one-line clarification avoids overclaiming; capturing "model still asked despite recall" is reasonably deferred to Goal 3 / hygiene.

---

A note on process: this session is in plan mode and the only tools available to me are Gmail/Calendar/Drive integrations — I have no file-write or plan-file tools here, so I am delivering the review verdict directly as requested rather than via a plan file. I made no edits and took no non-read-only actions.
