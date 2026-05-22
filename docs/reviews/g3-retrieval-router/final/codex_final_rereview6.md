Verdict: REQUEST_CHANGES

Critical issues: none.

Important issues:
- [agent/retrieval_router.py](/Users/jeremiah/.hermes/hermes-agent/agent/retrieval_router.py:172): `_url_is_public()` still treats URL userinfo credentials as public. Example: `what is latest at https://abc:xyz@example.com/news` plans `WEB_EXTRACT` with `private_query=False` and the full credential URL in `queries`. That avoids the old `WEB_SEARCH` fallback, but still places a credential URL on a public web route descriptor. Reject any URL with `urlparse(url).username` or `.password`, and add coverage for both `latest/current` and plain `summarize` forms.

Minor issues: none.

Prior blocker: partially fixed. Rejected query-token/profile URLs no longer fall through to `WEB_SEARCH`, but embedded-credential URLs remain unblocked.

Scope guard: I do not see remote writes, Chroma/Sentinel/Forge writes, `MEMORY.md`/`USER.md` writes, `memory_tool` semantic changes, or actual web/file/tool execution in this patch.