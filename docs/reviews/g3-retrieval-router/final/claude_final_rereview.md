# G3 Unified Retrieval Routing — Final Re-Review

## Verdict: REQUEST_CHANGES

The repair fixed most prior blockers, but the central one — "unsafe web public/private classification" — was applied only to `WEB_SEARCH`. Its sibling `WEB_EXTRACT` branch still carries the exact original bug pattern, so the deterministic privacy boundary Phase A exists to guarantee is still incomplete.

## Critical

**C1 — `WEB_EXTRACT` bypasses the repaired private/public classifier** (`agent/retrieval_router.py:194-210`)

The repair added `_PRIVATE_WEB_BLOCK_RE`/`_FILE_RE` checks to `_public_web_allowed()` (the `WEB_SEARCH` path) but left the `WEB_EXTRACT` branch gating only on `urls and not risk.mandatory`. `classify_risk()` flags `mandatory` for **only four** classes — `job_application`, `continuity`, `personal_profile`, `fleet_project` (`recall_gate.py:62-88`). Private topics outside those (medical, banking, health, salary) are not mandatory, so a private prompt with a URL still plans a web route with `private_query=False`:
- `"summarize my medical diagnosis at https://example.com/report"` → `web_extract`, `private_query=False`

This violates the contract boundary "web exclusion for private/profile/job/continuity prompts" (`G3_CONTRACT.md:44`) and a named pivot condition. Execution is deferred, but the descriptor — including `private_query`— is the artifact later executors are contract-checked against. The prior round rated this identical bug on `WEB_SEARCH` as Critical; consistency requires the same here.

## Important

- **I1 — LLM-refinement URL escape hatch** (`retrieval_router.py:280`). Web revalidation is `_web_query_is_public(q) or _URL_RE.fullmatch(q)` — the second clause accepts *any* full URL, so an LLM refinement can swap a deterministic public URL for a tokened/internal/profile URL while `private_query` stays `False`. Non-URL text is correctly revalidated; URLs are not. Default-off + deferred, but `merge_llm_refinement()` is contract-checked safety surface.
- **I2 — Secret-file denial bypassable with trailing punctuation** (`retrieval_router.py:129-135`). `sanitize_for_query()` does not strip punctuation, and `_SECRETISH_FILE_RE` is `$`-anchored — so `.env?`, `.env,`, `id_rsa.` are *not* denied and plan `file_read`/`file_search`. The repair added a denial test only for the bare `.env` happy path.

## Minor

- **M1** — `memory.retrieval_routing.allowed_routes` is in `DEFAULT_CONFIG` but consumed by nothing (planner hardcodes `RouteKind`); also absent from the contract's defaults YAML. Wire or drop.
- **M2** — `_PRIVATE_WEB_BLOCK_RE` includes `\bme\b`, suppressing common public phrasing ("give me the latest weather"). Fails safe but over-broad.
- **M3** — `FILE_READ` has no positive test; `G3_STATUS.md:67` still says "GREEN: 9 passed" though the suite now has 14 tests.

## Confirmed clean / fixed

Diff artifact now complete (11 files / 860 insertions; `git diff HEAD --stat` matches the artifact exactly — prior I2 fixed). Scope guard holds: pure module (stdlib + pure `recall_gate` only), no ChromaDB/Sentinel/Forge/`MEMORY.md`/`USER.md` writes, no `memory_tool` changes, no web/file/tool execution. `run_agent.py` is additive — config attributes stored but never read, G2 `enforced_recall()` untouched, no double injection; runtime-config regression test added. `WEB_SEARCH` classification repaired; `merge_llm_refinement` protects route kinds/budgets/mandatory floor. Config defaults + rollback match the contract. Bounded renderer dedups and respects budget.

**To reach APPROVED:** fix C1 (extend private/file exclusion to `WEB_EXTRACT`), harden I1 (revalidate refined URLs) and I2 (strip punctuation before secret matching + add variant tests), resolve M1/M3.

---

I've recorded the full review at the plan file. Note the controller's custody slot `docs/reviews/g3-retrieval-router/final/claude_final_rereview.md` is currently empty — it needs my verdict copied in (I cannot write repo files in plan mode). This is a read-only review deliverable with no implementation to approve, so I am not calling ExitPlanMode.
