Verdict: REQUEST_CHANGES

Critical issues: None.

Important issues:
1. Secret URL filtering is still too narrow in [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:79). Common credential query keys like `access_token`, `auth_token`, `session_id`, `bearer_token`, and camelCase variants like `authToken` are planned as `WEB_EXTRACT` with `private_query=False`. That violates the Phase A deterministic privacy guard for token/private URLs.

2. Secret file blocking is still too narrow in [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:72). The repaired `.env.local` and `path=.env` cases are fixed, but obvious credential paths like `~/.aws/credentials`, `.npmrc`, and secret directory paths like `secrets/config.yaml` still produce file-read descriptors. The contract says file routes deny obvious secret-file requests at planning time.

3. Public URLs containing file/path terms are misrouted as local file descriptors. For example, `summarize https://example.com/files/report` becomes `FILE_SEARCH` because `_FILE_RE` matches the URL text before web extraction is allowed. That violates the contract assertion that explicit public URL prompts can plan `web_extract`.

Minor issues: None beyond those.

Scope guard: I did not see remote writes, memory flat-file writes, Chroma/Sentinel/Forge writes, or web/file/tool execution added in this diff.