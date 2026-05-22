# G1C status — local memory doctor and Chroma readiness

## Status

Implemented on branch `feat/memory-g1c-doctor` as a post-G3 Goal 1 follow-on.

## Built

- Added `plugins/memory/chromadb/g1c_readiness.py`, a read-only readiness helper module.
- Added `hermes memory doctor` CLI with:
  - local-only default mode;
  - `--json` machine-readable report;
  - `--limit` JSONL summary limit;
  - opt-in `--probe` network checks;
  - `--strict` nonzero exit on failed executed checks.
- Surfaced the existing G1B feedback ledger (`memory_feedback.jsonl`) in CLI output, including G2 `recall_needed`, `recall_retrieved`, `recall_used`, and `recall_skipped` event counts.
- Preserved `hermes memory status` as a no-network config/status command.
- Added read-only Chroma probe semantics: heartbeat plus `get_collection().count()` for configured collections only.
- Added dependency-free Chroma HTTP v2 fallback for CLI environments where the optional `chromadb` Python package is absent; it uses heartbeat, collection listing, and collection-id count endpoints read-only.
- Added Forge probe semantics using the existing `/health` and `/embed` contract; `/embed-single` is intentionally not used.
- Added tests for local summaries, feedback recall event visibility, effective config defaults, strict exit behavior, default no-network behavior, read-only Chroma probing, and Forge endpoint shape.

## Safety

G1C does not write to:

- ChromaDB;
- Sentinel;
- Forge;
- `MEMORY.md`;
- `USER.md`;
- `boot_synthesis.jsonl`;
- `memory_feedback.jsonl`.

Network checks are opt-in with `--probe`.

## Verification commands

Focused G1C tests:

```bash
uv run --with pytest python -m pytest -o addopts='' -q \
  tests/plugins/memory/test_chromadb_g1c_readiness.py \
  tests/hermes_cli/test_memory_doctor.py
```

Expanded memory regression:

```bash
uv run --with pytest python -m pytest -o addopts='' -q \
  tests/plugins/memory/test_chromadb_g1c_readiness.py \
  tests/hermes_cli/test_memory_doctor.py \
  tests/plugins/memory/test_chromadb_g1b_observability.py \
  tests/hermes_cli/test_memory_receipts.py \
  tests/plugins/memory/test_chromadb_g1a_boot_synthesis.py \
  tests/plugins/memory/test_chromadb_generated_profile.py \
  tests/plugins/memory/test_chromadb_provider.py \
  tests/plugins/memory/test_chromadb_g2_recall.py \
  tests/run_agent/test_g2_recall_wiring.py \
  tests/agent/test_recall_gate.py \
  tests/agent/test_retrieval_router.py \
  tests/run_agent/test_memory_prompt_source.py
```

Live read-only connectivity smoke:

```bash
uv run python -m hermes_cli.main memory doctor --probe --json --strict
```

Verified result on the MacBook Rilo config:

- compile check passed;
- expanded memory regression: `147 passed`;
- live doctor strict probe: `ok=True`;
- Chroma probe: `ok=True`, transport `http-v2`, configured collections counted;
- Forge probe: `ok=True`, `/health` dimensions `1024`, `/embed` dimensions `1024`.
- final blocker fix added a regression for valid but non-object `chromadb.json`; doctor now reports `ok=False` instead of crashing.
- final review nits resolved: feedback summaries expose `missing`, Chroma double-failure returns fallback error details, Forge exception handling simplified, and contract docs include `--limit`.

## Deferred

- Chroma fact cleanup, dedup review queues, deletion/supersession workflows.
- Cross-tool G3 execution receipts.
- Dashboard visualization.
- Dynamic salience tuning from accumulated feedback.
