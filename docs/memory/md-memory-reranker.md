# Manifold Destiny Memory Reranker
2|
3|Parameter-free Fisher-Rao reranking for agent memory retrieval.
4|
5|## Overview
6|
7|The Manifold Destiny reranker improves memory retrieval quality without
8|neural rankers, learned weights, or additional model calls. It applies
9|information geometry — the Fisher-Rao metric on a categorical probability
10|simplex — to rerank candidates returned by a vector database.
11|
12|**Embedding-agnostic.** The module operates on plain Python dicts
13|(`{"id": ..., "content": ..., "metadata": ...}`) returned by any vector
14|database — ChromaDB, Pinecone, FAISS, or any custom provider. It does not
15|call embedding services, does not depend on any specific embedding model, and
16|does not make network calls. It is pure post-processing on candidate rows that
17|the vector DB has already retrieved.
18|
19|This separation matters: the reranker cares about the **typed semantic
20|structure** of each memory (subjects, predicates, claim types, dates, facets),
21|not about the embedding space. Two memories with similar embeddings but
22|different semantic types ("prefers concise" vs "prefers detailed") are correctly
23|separated because their typed atoms differ, not because their vectors differ.
24|
25|This implements the consumer-relative validity criterion and Fisher-Rao
26|retrieval geometry from:
27|
28|> Thompson & Horowitz, "Manifold Destiny: Continuous Learning by Consumption
29|> of Truth-Verified Structure from the Zero-Information Floor" (2026).
30|>
31|> _Citation will be updated with DOI/arXiv link upon publication._
> _Code, test artifacts, and quantum data: https://github.com/manifold-destiny/manifold-destiny (public upon paper release)._
32|
33|The Fisher-Rao distance uses `arccos(BC)` (range `[0, π/2]`); the canonical
34|Fisher-Rao geodesic is `2·arccos(BC)` (range `[0, π]`). The factor-of-2
35|difference is irrelevant for ranking since arccos is monotone.
36|
37|## How It Works
38|
39|### 1. Chart Atoms (Observation Chart)
40|
41|Each memory is mapped to weighted semantic atoms using a declarative chart:
42|
43|| Atom Type | Source | Weight |
44||-----------|--------|--------|
45|| `subject:` | Proper nouns, named entities | 12.0 |
46|| `resolved_date:` | ISO dates (2026-06-29) | 10.0 |
47|| `question_type:` | When/where/who/what/why/how | 8.0 |
48|| `predicate:` | Action verbs (normalized) | 8.5 |
49|| `object:` | Key content nouns | 8.5 |
50|| `place:` | Location terms | 7.5 |
51|| `claim_type:` | Semantic category | 5.0–6.0 |
52|| `facet:` | Detected facets (time, person, etc.) | 5.0 |
53|| `kw:` | Top keywords (normalized) | 2.4–3.0 |
54|| `tok:` | Content tokens (IDF-scaled) | 2.0–4.5 |
55|
56|### 2. Probability Simplex
57|
58|Atom weights are L1-normalized to a probability distribution. This places
59|each memory at a point on the categorical probability simplex.
60|
61|### 3. Fisher-Rao Distance
62|
63|Retrieval distance is the geodesic on this simplex — the Fisher-Rao metric:
64|
65|```
66|d(p, q) = arccos( Σᵢ √(pᵢ · qᵢ) )
67|```
68|
69|This is the same metric used in the Fisher information matrix and Amari's
70|information geometry. Range: `[0, π/2]`. Zero means identical distributions;
71|`π/2` means no shared atoms.
72|
73|### 4. Validity Penalties
74|
75|Declarative constraints are layered on top of the geometric distance:
76|
77|| Constraint | Penalty |
78||-----------|---------|
79|| `status=active` | −0.05 (boost) |
80|| `status=superseded` | +0.75 (quarantine) |
81|| `status=decoy` | +0.30 (penalize) |
82|| Claim-type overlap | up to −0.18 |
83|| Subject overlap | up to −0.24 |
84|| Exact date match | −0.26 |
85|| Month match | −0.16 |
86|| Keyword overlap | up to −0.22 |
87|| Missing required scope | +0.10 to +0.50 |
88|
89|### 5. Blended Reranking
90|
91|The final rank blends the FI rank with the original vector database rank:
92|
93|```
94|combined = (1 − w) × original_rank + w × fi_rank
95|```
96|
97|where `w` is the `score_weight` parameter (default: 0.35).
98|
99|## Configuration
100|
101|The reranker is controlled by config keys in whatever config your provider
102|reads (e.g. `chromadb.json`, `config.yaml`, or inline dict). It does not
103|need its own config file — it reads from the provider's config section.
104|
105|Provider-agnostic config shape:
106|
107|```python
108|# Any provider can pass these to rerank_rows():
109|rerank_rows(
110|    query,
111|    candidates,
112|    score_weight=0.35,      # blend weight (0=original order, 1=pure FI)
113|    max_candidates=80,       # cap on candidates to rerank
114|    annotate=True,           # add md_score/md_distance/md_penalty to results
115|)
116|```
117|
118|For ChromaDB-provider-specific config (in `chromadb.json`):
119|
120|```json
121|{
122|  "md_reranker": {
123|    "enabled": true,
124|    "score_weight": 0.35,
125|    "candidate_multiplier": 4,
126|    "max_candidates": 80,
127|    "min_candidates": 3,
128|    "annotate_results": true
129|  }
130|}
131|```
132|
133|When disabled, the provider falls through to standard composite scoring
134|(similarity + recency + importance).
135|
136|## Performance
137|
138|Benchmark results (no ChromaDB dependency required):
139|
140|| Test | Threshold | Metric |
141||------|-----------|--------|
142|| Paraphrase recall@3 | ≥ 80% | 5 queries × 6 gold memories |
143|| Near-negative discrimination | pass | Distinguishes similar memories |
144|| Supersession ordering | pass | Active outranks superseded |
145|| Batch exact recall@3 | ≥ 95% | 50 unique-token items |
146|| Decoy quarantine | pass | Decoys penalized vs real |
147|
148|## Theory
149|
150|### Consumer-Relative Validity (Definition 1)
151|
152|A memory `q` is relevant for a downstream consumer `c` only if it preserves
153|every distinction the consumer needs:
154|
155|```
156|∀ x, y: q(x) = q(y) → c(x) = c(y)
157|```
158|
159|The chart atoms operationalize this: the query's atoms define what the
160|consumer cares about, and validity penalties enforce that memories matching
161|the consumer's typed coordinates are ranked higher.
162|
163|### The Zero-Information Floor
164|
165|Without FI reranking, retrieval sits at the similarity-only baseline. The
166|composite score (cosine similarity × recency × importance) carries no
167|information about the semantic type structure of the query. FI reranking is
168|the displacement from this floor — each typed atom match is a certified
169|information gain.
170|
171|### Parameters Are Not Primitive
172|
173|The FI reranker has no learned parameters. All weights are fixed by the typed
174|structure of the claim/event frame. This means:
175|
176|- No training data required
177|- No overfitting to a specific domain
178|- No drift over time
179|- Transparent, auditable ranking decisions
180|
181|## Write-Path Enrichment
182|
183|When memories are stored, the `extract_claim_event_frame()` function can be
184|used to pre-compute typed metadata:
185|
186|```python
187|from plugins.memory.chromadb.md_reranker import extract_claim_event_frame
188|
189|frame = extract_claim_event_frame("User prefers concise responses.")
190|metadata = {
191|    "claim_types_csv": ",".join(frame["claim_types"]),
192|    "subjects_csv": ",".join(frame["subjects"]),
193|    "predicates_csv": ",".join(frame["predicates"]),
194|    "facets_csv": ",".join(frame["facets"]),
195|    "keywords_csv": ",".join(frame["keywords"]),
196|}
197|```
198|
199|Pre-computed metadata makes retrieval faster (no text re-parsing) and more
200|consistent (same normalization at write and read time).
201|
202|## Backward Compatibility
203|
204|- **Disabled by default** (`md_reranker.enabled: false`)
205|- When disabled, falls through to existing composite scoring
206|- No changes to embedding dimensions or collection schema
207|- No changes to memory tool API
208|- No external dependencies beyond the Python standard library
209|
210|## Next Research Directions
211|
212|These follow naturally from the paper's framework and the implementation's
213|current limitations:
214|
215|### 1. Self-extending chart atoms (paper: self-extending theorem)
216|
217|Retained quotients promote to new grammar atoms (Section 4 of the paper).
218|Currently, chart atoms are fixed. A natural extension: when a memory is
219|repeatedly recalled and confirmed useful, its distinctive atoms promote to
220|first-class chart coordinates — letting the reranker learn domain-specific
221|vocabulary.
222|
223|### 2. Explicit consumer-scope injection (paper: consumer c as specification)
224|
225|Currently, consumer scope is inferred from query text. The paper treats the
226|consumer as an explicit specification. A provider could pass structured task
227|context as an explicit consumer-scope parameter, making the validity criterion
228|sharper than keyword inference.
229|
230|### 3. Calibrated validity penalties (paper: calibrated retention)
231|
232|Validity penalty magnitudes are hand-set. These could be calibrated from user
233|feedback — turning the declarative penalties into a calibrated retention layer
234|without making them learned weights.
235|
236|### 4. Write-path verification gate (paper: verifier V as trust boundary)
237|
238|The paper's verifier is the trust boundary — only verified structure is
239|retained. A stronger write gate could use the chart atoms themselves: require
240|minimum atom count or claim-type coverage before accepting a memory.
241|
242|### 5. Cross-collection fiber merging (paper: same-fiber alias merge)
243|
244|The paper's manifold store merges distinct surface expressions with the same
245|verified fiber. Same-fiber merging across collections would reduce redundancy
246|and improve recall consistency.
247|