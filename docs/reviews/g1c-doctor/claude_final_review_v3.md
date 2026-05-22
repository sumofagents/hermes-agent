I have full context now. Let me verify the key behavioral claims against the actual G1B reader implementation.

The critical question for the feedback-`missing` nit: the doctor calls `read_feedback_events(...).tail(limit)`, which returns a `JsonlRecords` (a `list` subclass carrying `.malformed_count` and `.missing`). `_read_jsonl` returns `JsonlRecords([], malformed_count=0, missing=True)` for an absent file, and `.tail()` preserves both attributes (g1b_observability.py:61-64). So in `summarize_feedback_events`, path A runs (`records` has `malformed_count`), and `missing` is read straight from the attribute — the doctor correctly surfaces `missing: True` for an absent ledger. The new unit test exercises the same object type. Both paths verified correct.

---

Verdict: APPROVED

Critical Issues:
- None. The four nit fixes do not introduce any blocker, and read-only safety is preserved (no new writes; probes remain heartbeat/GET-count/`GET /health`/`POST /embed`, with `get_or_create_collection` still never called).

Important Issues:
- None.

Minor Issues:
- `probe_chroma` double-failure branch (g1c_readiness.py:140-142) is dead-code: `if fallback.get("ok"): return fallback` and the `else` both `return fallback`. Functionally correct (always returns the fallback dict with `client_error` attached, as the test asserts), but the conditional is vestigial and should collapse to a single `return fallback`.
- `summarize_feedback_events` line 192: `bool(getattr(records, "missing", False)) if hasattr(records, "missing") else False` — the `if hasattr(...) else False` is fully redundant since `getattr(..., False)` already defaults. `summarize_boot_receipts` reuses its `has_missing_attr` var for the path-B gate (line 121), but the feedback function gates path B on `malformed_count` instead, so the `hasattr` here is computed and discarded. Cosmetic; behavior is correct for both `JsonlRecords` and plain-list inputs.
- The human-readable doctor output (`cmd_doctor`) prints feedback `event_count`/`malformed_count`/`event_types` but not the new `missing` flag — it's only visible via `--json`. Acceptable per the nit ("summary includes missing"), but surfacing it in text mode would be consistent with the missing-file-visibility intent.

Notes:
- Feedback `missing` now reaches parity with boot (`summarize_boot_receipts` already exposed `missing`). Verified end-to-end: missing ledger → `JsonlRecords(missing=True)` → `.tail()` preserves it → path A → `summary["missing"] is True`, and no file is created (matches the new RED-confirmed test).
- Non-object `chromadb.json` regression fix is sound: `load_chromadb_json_status` returns `ok=False` with a typed error and never touches `from_json_file` on the default path. Separately, the `--probe` path still calls `ChromaDBConfig.from_json_file` unguarded — pre-existing, already-approved code outside this nit set, and the live strict probe confirms it works on the real config; flagging only for awareness, not as a change request.
- Forge exception simplification correctly keeps `POST /embed` (never `/embed-single`) and the catch-all returns a clean `{ok: False, url, error}` — read-only contract intact.
- Contract `--limit` example and STATUS doc are consistent with the implemented `--limit` arg (default 100, clamped `max(0, ...)` in both `cmd_doctor` and `build_readiness_report`).
- Existing G1B tests use key-access rather than exact-dict equality, so the new `missing` key does not break them — consistent with the reported `147 passed`.
