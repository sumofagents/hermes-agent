Verdict: REQUEST_CHANGES

**Critical**
- [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:194): `WEB_EXTRACT` bypasses the repaired private/public classifier. The URL branch only checks `not risk.mandatory`, so private prompts still plan external web routes, e.g. `summarize my medical diagnosis at https://example.com/report` and URLs containing `token=...` both produce `web_extract` with `private_query=False`. This violates the contract’s “web exclusion for private/profile/job/continuity prompts” boundary. Apply the same private/file/secret classification to URL extraction, or add a stricter URL-specific classifier.

**Important**
- [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:280): LLM refinement revalidates private `web_search` text now, but full URLs are always accepted via `_URL_RE.fullmatch(q)`. For an existing public `web_extract` route, an LLM refinement can replace `https://example.com/research` with `https://example.com/?token=abc123` or `/users/jeremiah/profile` and keep `private_query=False`.
- [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:132): secret-file denial is too easy to bypass with punctuation. `read file .env` is denied, but `read file .env?`, `.env,`, or `id_rsa.` plan `file_read`. The negative test at [tests/agent/test_retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/tests/agent/test_retrieval_router.py:97) should cover punctuation and path variants.

**Minor**
- `memory.retrieval_routing.allowed_routes` is still only a default value; neither runtime nor planner consumes it. Fine as documented future config, but it has no Phase A semantics yet.
- `FILE_READ` has no positive test despite being a declared route descriptor.

Prior blockers are mostly repaired for `web_search`: non-G2 private freshness, repo freshness, refined private search queries, runtime config initialization, and the main diff artifact are improved. Runtime integration remains additive and does not change G2 injection or cause double injection. I found no ChromaDB/Sentinel/Forge writes, no `MEMORY.md`/`USER.md` writes, no `memory_tool` semantic changes, and no web/file/tool execution in this diff.