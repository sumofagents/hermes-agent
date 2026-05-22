Verdict: REQUEST_CHANGES

Critical issues:
- [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:115): `WEB_SEARCH` privacy classification still lets job/private relationship prompts route to public web if they avoid the exact G2/regex terms. Example probe: `what is the latest news about the job I applied to at Anduril?` plans `web_search` with the full private query and `private_query=False`. This violates the G3 contract’s job/private web exclusion.
- [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:301): `merge_llm_refinement()` revalidates web routes but not file routes. A safe deterministic `FILE_READ` plan for `docs/memory/G3_CONTRACT.md` can be refined to `read file .env`, bypassing the secret-file guard at [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:176). The LLM boundary must re-run file query validation for `FILE_READ`/`FILE_SEARCH`.

Important issues:
- None beyond the two blockers above.

Minor issues:
- [hermes_cli/config.py](/Users/jeremiah/.hermes/hermes-agent/hermes_cli/config.py:1173): `memory.retrieval_routing.allowed_routes` is still documented as a default but unused by runtime/planner. Acceptable as inert future config, but it has no Phase A semantics.

The prior repaired blockers for URL privacy, LLM web URL refinement, punctuation-hardened secret-file detection, diff completeness, runtime config storage, and added tests are mostly addressed. Scope guard otherwise holds: no web/file/tool execution, no Chroma/Sentinel/Forge writes, no `MEMORY.md`/`USER.md` writes, and no `memory_tool` semantic changes.