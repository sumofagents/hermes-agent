# G3 final review packet

## Verification already run
- Initial RED: tests/agent/test_retrieval_router.py failed with ModuleNotFoundError before agent/retrieval_router.py existed.
- Reviewer RED #1: privacy-negative tests failed for non-G2 private freshness prompts, repo freshness prompts, and LLM-refined private web queries before repair.
- Reviewer RED #2: URL/privacy and secret-file punctuation tests failed before repair.
- Reviewer RED #3: private job-relationship web search and file-route LLM secret rewrite tests failed before repair.
- Reviewer RED #4: .env.local/path=.env and tell-me-public-web tests failed before repair.
- Reviewer RED #5: credential URL keys, secret credential paths, and public URL file/path-term tests failed before repair.
- Reviewer RED #6: rejected credential/private URLs fell through to WEB_SEARCH before repair.
- Reviewer RED #7: URL userinfo credentials planned WEB_EXTRACT before repair.
- Reviewer RED #8: localhost/private/link-local/.local URL targets planned WEB_EXTRACT before repair.
- Reviewer RED #9: alternate numeric loopback and bracketed IPv6 URL forms planned WEB_EXTRACT or raised before repair.
- Reviewer RED #10: mixed-radix numeric loopback hosts and non-global IP literals planned WEB_EXTRACT before repair.
- Reviewer RED #11: multicast/site-local special-use IP literals planned WEB_EXTRACT before repair.
- uv run --with pytest python -m pytest -o addopts="" -q tests/agent/test_retrieval_router.py => 22 passed
- uv run --with pytest python -m pytest -o addopts="" -q tests/agent/test_retrieval_router.py tests/hermes_cli/test_g3_config_defaults.py tests/agent/test_recall_gate.py tests/plugins/memory/test_chromadb_g2_recall.py tests/run_agent/test_g2_recall_wiring.py tests/plugins/memory/test_chromadb_g1b_observability.py tests/hermes_cli/test_memory_receipts.py => 64 passed
- uv run python -m py_compile agent/retrieval_router.py run_agent.py hermes_cli/config.py => passed
- git diff --check => passed

## Final reviewer verdicts
- Claude Opus 4.7 final rereview2: APPROVED; no Critical/Important issues after early privacy repairs.
- Codex GPT-5.5 final rereview11: APPROVED; prior IP literal blocker fixed; no Critical/Important/Minor issues under Phase A contract.

## Git status at packet regeneration
 A agent/retrieval_router.py
 A docs/memory/G3_CONTRACT.md
 A docs/memory/G3_STATUS.md
 A docs/reviews/g3-retrieval-router/README.md
 A docs/reviews/g3-retrieval-router/claude_plan_review.md
 A docs/reviews/g3-retrieval-router/codex_plan_review.md
 M hermes_cli/config.py
 M run_agent.py
 A tests/agent/test_retrieval_router.py
 A tests/hermes_cli/test_g3_config_defaults.py
 M tests/run_agent/test_g2_recall_wiring.py
?? docs/reviews/g3-retrieval-router/final/

## Diff stat including intent-to-add files
 agent/retrieval_router.py                          | 504 +++++++++++++++++++++
 docs/memory/G3_CONTRACT.md                         | 112 +++++
 docs/memory/G3_STATUS.md                           |  72 +++
 docs/reviews/g3-retrieval-router/README.md         |   8 +
 .../g3-retrieval-router/claude_plan_review.md      |  37 ++
 .../g3-retrieval-router/codex_plan_review.md       |  65 +++
 hermes_cli/config.py                               |  20 +
 run_agent.py                                       |   5 +
 tests/agent/test_retrieval_router.py               | 280 ++++++++++++
 tests/hermes_cli/test_g3_config_defaults.py        |  14 +
 tests/run_agent/test_g2_recall_wiring.py           |   7 +
 11 files changed, 1124 insertions(+)
