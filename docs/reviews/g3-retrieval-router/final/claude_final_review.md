The review is complete. Full report written to the plan file. Here is the verdict.

---

# G3 Unified Retrieval Routing — Final Review

## Verdict: REQUEST_CHANGES

The scope guard holds and most contract assertions are met, but the deterministic private/public web classification — the central safety deliverable of Phase A — is wrong, so it cannot serve as the contract-checkable foundation for later executors.

## Critical

**C1 — Web privacy classification treats "not G2-mandatory" as "public"** (`agent/retrieval_router.py:103`)
`_public_web_allowed()` excludes web routing only when `risk.mandatory` is true. G2 `classify_risk()` flags *only* `job_application`, `continuity`, `personal_profile`, `fleet_project`. Any other private topic with a freshness keyword gets a `web_search` descriptor with `private_query=False`:
- `"what is the latest about my medical diagnosis?"` → `web_search`, `private_query=False`
- `"what is the latest in this repo for X"` → **both** public `web_search` and private `file_search`

The web query is the verbatim user message (`_dedup([message], limit=1)`) — private clauses are carried in unscrubbed. This violates the contract's deterministic private/public boundary (`G3_CONTRACT.md:39-48`) and is a named pivot condition. Execution is deferred so there's no live leak today, but the descriptor *is* the artifact later executors are contract-checked against — a wrong `private_query=False` makes that check unsound. G2 recall risk is not a complete public-query classifier.

## Important

- **I1** — `merge_llm_refinement()` (`retrieval_router.py:251`) does not re-validate rewritten queries: for an already-present `web_search` route the LLM may replace `queries` with arbitrary private text while `private_query` stays `False`. Route kinds/budgets/mandatory floor are correctly protected; query *content* for an external route is not. Contract permits query rewriting, so not a strict violation — but combined with C1 it's the concrete leak path; harden before web execution.
- **I2** — `pr.diff` contains only `config.py` + `run_agent.py` (25 insertions). `agent/retrieval_router.py`, both test files, and the G3 docs are untracked and absent. The "exact diff" excludes the entire Phase A implementation; regenerate it.
- **I3** — Contract assertion #11 (runtime init reads G3 config without changing G2 semantics) has no test. *Verified manually:* the stored attributes are never read elsewhere and the G2 path at `run_agent.py:12720` is untouched — no double injection — but the contract demands a regression test.
- **I4** — Missing privacy-negative tests: non-G2 private prompt + freshness word (the C1 case), repo/file prompt + freshness word, the secret-file denial path, refined web query with private text, and the `enabled=False` path.

## Minor

- **M1** — `memory.retrieval_routing.allowed_routes` is unused (planner hardcodes `RouteKind`) and not in the contract's defaults block. Wire it or drop it.
- **M2** — `_SECRETISH_FILE_RE` `.*key.*`/`.*token.*` match innocuous tokens ("monkey" → "key"); safe direction but imprecise.
- **M3** — `RouteKind.FILE_READ` has no test; `tool_recall` has no planner path (expected for deferral).

## Confirmed clean

Pure module (stdlib + pure `recall_gate` only); no ChromaDB/Sentinel/Forge/`MEMORY.md`/`USER.md` writes; no `memory_tool` changes; no web/file/tool execution. `merge_llm_refinement` cannot add route kinds, raise budgets, lower the mandatory flag, or drop routes. `run_agent.py` change is additive-only — G2 enforced-recall path unchanged, no double injection. Config defaults and rollback switch match the contract. Bounded renderer deduplicates and respects the budget.

**To reach APPROVED:** fix C1 (deterministic non-web exclusion for personal/private and local/repo prompts), address I1, regenerate `pr.diff` with the untracked files, and add the I3/I4 tests.

*(Note: I have not called ExitPlanMode — this is a review deliverable, not an implementation plan. The full report is saved at the plan file path for your reference.)*
