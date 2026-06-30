# Fisher-Rao Pullback Memory Reranker

Parameter-free Fisher-Rao reranking for agent memory retrieval.

## Overview

The Fisher-Rao pullback reranker improves memory retrieval quality without
neural rankers, learned weights, or additional model calls. It applies
information geometry — the Fisher-Rao metric on a categorical probability
simplex — to rerank candidates returned by a vector database.

This implements the consumer-relative validity criterion and Fisher-Rao
retrieval geometry from:

> Thompson & Horowitz, "Manifold Destiny: Continuous Learning by Consumption
> of Truth-Verified Structure from the Zero-Information Floor" (2026).
>
> _Citation will be updated with DOI/arXiv link upon publication._

The Fisher-Rao distance uses `arccos(BC)` (range `[0, π/2]`); the canonical
Fisher-Rao geodesic is `2·arccos(BC)` (range `[0, π]`). The factor-of-2
difference is irrelevant for ranking since arccos is monotone.

## How It Works

### 1. Chart Atoms (Observation Chart)

Each memory is mapped to weighted semantic atoms using a declarative chart:

| Atom Type | Source | Weight |
|-----------|--------|--------|
| `subject:` | Proper nouns, named entities | 12.0 |
| `resolved_date:` | ISO dates (2026-06-29) | 10.0 |
| `question_type:` | When/where/who/what/why/how | 8.0 |
| `predicate:` | Action verbs (normalized) | 8.5 |
| `object:` | Key content nouns | 8.5 |
| `place:` | Location terms | 7.5 |
| `claim_type:` | Semantic category | 5.0–6.0 |
| `facet:` | Detected facets (time, person, etc.) | 5.0 |
| `kw:` | Top keywords (normalized) | 2.4–3.0 |
| `tok:` | Content tokens (IDF-scaled) | 2.0–4.5 |

### 2. Probability Simplex

Atom weights are L1-normalized to a probability distribution. This places
each memory at a point on the categorical probability simplex.

### 3. Fisher-Rao Distance

Retrieval distance is the geodesic on this simplex — the Fisher-Rao metric:

```
d(p, q) = arccos( Σᵢ √(pᵢ · qᵢ) )
```

This is the same metric used in the Fisher information matrix and Amari's
information geometry. Range: `[0, π/2]`. Zero means identical distributions;
`π/2` means no shared atoms.

### 4. Validity Penalties

Declarative constraints are layered on top of the geometric distance:

| Constraint | Penalty |
|-----------|---------|
| `status=active` | −0.05 (boost) |
| `status=superseded` | +0.75 (quarantine) |
| `status=decoy` | +0.30 (penalize) |
| Claim-type overlap | up to −0.18 |
| Subject overlap | up to −0.24 |
| Exact date match | −0.26 |
| Month match | −0.16 |
| Keyword overlap | up to −0.22 |
| Missing required scope | +0.10 to +0.50 |

### 5. Blended Reranking

The final rank blends the FI rank with the original vector database rank:

```
combined = (1 − w) × original_rank + w × fi_rank
```

where `w` is the `score_weight` parameter (default: 0.35).

## Configuration

```yaml
# config.yaml
memory:
  provider: chromadb
  fi_reranker:
    enabled: true
    score_weight: 0.35        # blend weight (0=original, 1=pure FI)
    candidate_multiplier: 4   # over-fetch factor
    max_candidates: 80        # hard cap on candidates to rerank
    min_candidates: 3         # skip reranking below this count
    annotate_results: true    # add fi_score/fi_distance/fi_penalty to results
```

When disabled, the provider falls through to standard composite scoring
(similarity + recency + importance).

## Performance

Benchmark results (no ChromaDB dependency required):

| Test | Threshold | Metric |
|------|-----------|--------|
| Paraphrase recall@3 | ≥ 80% | 5 queries × 6 gold memories |
| Near-negative discrimination | pass | Distinguishes similar memories |
| Supersession ordering | pass | Active outranks superseded |
| Batch exact recall@3 | ≥ 95% | 50 unique-token items |
| Decoy quarantine | pass | Decoys penalized vs real |

## Theory

### Consumer-Relative Validity (Definition 1)

A memory `q` is relevant for a downstream consumer `c` only if it preserves
every distinction the consumer needs:

```
∀ x, y: q(x) = q(y) → c(x) = c(y)
```

The chart atoms operationalize this: the query's atoms define what the
consumer cares about, and validity penalties enforce that memories matching
the consumer's typed coordinates are ranked higher.

### The Zero-Information Floor

Without FI reranking, retrieval sits at the similarity-only baseline. The
composite score (cosine similarity × recency × importance) carries no
information about the semantic type structure of the query. FI reranking is
the displacement from this floor — each typed atom match is a certified
information gain.

### Parameters Are Not Primitive

The FI reranker has no learned parameters. All weights are fixed by the typed
structure of the claim/event frame. This means:

- No training data required
- No overfitting to a specific domain
- No drift over time
- Transparent, auditable ranking decisions

## Write-Path Enrichment

When memories are stored, the `extract_claim_event_frame()` function can be
used to pre-compute typed metadata:

```python
from plugins.memory.chromadb.fi_reranker import extract_claim_event_frame

frame = extract_claim_event_frame("User prefers concise responses.")
metadata = {
    "claim_types_csv": ",".join(frame["claim_types"]),
    "subjects_csv": ",".join(frame["subjects"]),
    "predicates_csv": ",".join(frame["predicates"]),
    "facets_csv": ",".join(frame["facets"]),
    "keywords_csv": ",".join(frame["keywords"]),
}
```

Pre-computed metadata makes retrieval faster (no text re-parsing) and more
consistent (same normalization at write and read time).

## Backward Compatibility

- **Disabled by default** (`fi_reranker.enabled: false`)
- When disabled, falls through to existing composite scoring
- No changes to embedding dimensions or collection schema
- No changes to memory tool API
- No external dependencies beyond the Python standard library

## Next Research Directions

These follow naturally from the paper's framework and the implementation's
current limitations:

### 1. Self-extending chart atoms (paper: self-extending theorem)

Retained quotients promote to new grammar atoms (Section 4 of the paper).
Currently, chart atoms are fixed. A natural extension: when a memory is
repeatedly recalled and confirmed useful, its distinctive atoms promote to
first-class chart coordinates — letting the reranker learn domain-specific
vocabulary.

### 2. Explicit consumer-scope injection (paper: consumer c as specification)

Currently, consumer scope is inferred from query text. The paper treats the
consumer as an explicit specification. A provider could pass structured task
context as an explicit consumer-scope parameter, making the validity criterion
sharper than keyword inference.

### 3. Calibrated validity penalties (paper: calibrated retention)

Validity penalty magnitudes are hand-set. These could be calibrated from user
feedback — turning the declarative penalties into a calibrated retention layer
without making them learned weights.

### 4. Write-path verification gate (paper: verifier V as trust boundary)

The paper's verifier is the trust boundary — only verified structure is
retained. A stronger write gate could use the chart atoms themselves: require
minimum atom count or claim-type coverage before accepting a memory.

### 5. Cross-collection fiber merging (paper: same-fiber alias merge)

The paper's manifold store merges distinct surface expressions with the same
verified fiber. Same-fiber merging across collections would reduce redundancy
and improve recall consistency.
