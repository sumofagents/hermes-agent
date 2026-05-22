Verdict: REQUEST_CHANGES

**Critical Issues**
- [agent/retrieval_router.py:103](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:103): web routing treats “not G2 mandatory” as public. Prompts like `what is the latest about my medical diagnosis?` plan `web_search` with `private_query=False`, and `what is the latest in this repo...` plans both public web and private file routes. This violates the contract’s private/public boundary and pivot condition in [G3_CONTRACT.md:39](/Users/jeremiah/.hermes/hermes-agent/docs/memory/G3_CONTRACT.md:39). G2 recall risk is not a complete public-query classifier.
- [agent/retrieval_router.py:251](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:251): `merge_llm_refinement()` lets an LLM replace an already-allowed `web_search` query with arbitrary private text. It blocks new route kinds and budget escalation, but it does not revalidate refined web queries against deterministic public/private rules.

**Important Issues**
- [docs/reviews/g3-retrieval-router/final/pr.diff:1](/Users/jeremiah/.hermes/hermes-agent/docs/reviews/g3-retrieval-router/final/pr.diff:1): the supplied PR diff only contains `hermes_cli/config.py` and `run_agent.py`. The actual router, tests, and G3 docs are untracked in the working tree, so the exact diff artifact does not contain the Phase A implementation it claims to review.
- Test coverage should add privacy-negative cases for non-G2 private prompts, local repo/file prompts with freshness words, and LLM-refined web queries containing private facts. The current private-web test only covers a G2-mandatory job/profile phrase.

**Minor Issues**
- `memory.retrieval_routing.allowed_routes` is added to defaults but is not consumed by the planner or runtime. Either wire it into planning later under the Phase A contract or remove it until it has semantics.

No evidence of ChromaDB/Sentinel/Forge writes, `MEMORY.md`/`USER.md` writes, web/file/tool execution, or `memory_tool` semantic changes in the inspected code. G2 runtime injection appears unchanged because `run_agent.py` only stores the new retrieval config attributes.