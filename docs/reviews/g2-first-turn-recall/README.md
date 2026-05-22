# G2 first-turn semantic recall contract review custody

This directory preserves the Phase 1 docs-only gate receipts for `docs/memory/G2_CONTRACT.md`.

Scope:
- MacBook Rilo only.
- Docs-only contract PR.
- No runtime implementation.
- No ChromaDB writes, schema changes, or Sentinel/Forge restarts.
- No MEMORY.md or USER.md mutations.
- Stop before merge unless controller explicitly authorizes.

Gate results:
- planctl: pass, completeness_score 1.0, deficiencies [].
- Codex GPT-5.5 read-only lane: APPROVED.
- Claude Opus 4.7 read-only lane: APPROVED.
- Claude OAuth/Max smoke: succeeded before final Opus review.

Files:
- `planctl_evaluate.json` — final planctl evaluation result.
- `codex_gpt55_review.md` — final Codex GPT-5.5 review verdict.
- `claude_opus47_review.md` — final Claude Opus 4.7 review verdict.
- `claude_oauth_smoke.json` — local Claude Code OAuth/Max smoke proving Opus 4.7 lane health before review.
- `FILE_MANIFEST.txt` — file list and sizes.
- `SHA256SUMS.txt` — SHA-256 checksums for custody files.
