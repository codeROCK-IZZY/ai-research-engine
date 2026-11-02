# Known limitations

## Retrieval and answers
- **Benchmark tuning.** The relevance-gate threshold (0.2) and the offline answerer's sentence rules were chosen while looking at the same synthetic benchmark the report uses, so its answer figures are optimistic for unseen corpora. A separate held-out question set would give an unbiased number.
- **Offline answers quote, they don't write.** Without an API key the answer is the best one or two source sentences. Questions phrased very differently from the source (paraphrases) are answered correctly about half the time in the benchmark; with Claude configured, answers are generated from the retrieved passages instead.
- **Re-ranking costs CPU.** The cross-encoder runs on CPU and adds roughly a second or more per query on a laptop (see `eval/reports`). It can be switched off per query (`rerank=false`) or globally (`RKE_RERANK_BY_DEFAULT=false`), falling back to the hybrid order and the vector-similarity relevance gate.
- **Conflict detection is a rule.** It compares quantities (numbers with units) that follow the same question term in cited passages from different sources. It does not understand prose disagreements ("is allowed" vs "is not allowed").
- **Vector search is an exact scan** over the chunks the user may read (always correct with access control). Beyond roughly 100k chunks per user, partitioning or a filtered approximate index is needed.

## Knowledge features
- **Concepts are found by patterns** (acronyms, capitalised multi-word names), not a trained entity model, so some common capitalised phrases appear and some lower-case concepts are missed.
- **Topics** use k-means with k chosen from the number of sources; there is no automatic choice of the best k.
- **Near-duplicate detection is pairwise (O(n²))** within a knowledge base, fine for thousands of sources; LSH banding is the scale-up path.
- **Freshness and reliability are rule-based indicators**, not judgements about content accuracy.

## Connectors
- **One web page per request; no crawling, no JavaScript rendering.** Pages that build their content in the browser yield little text.
- **No scheduled refresh.** Web sources are refreshed on demand (per source or per knowledge base); a scheduler is not included.
- Fetching runs inside the API request (15 s timeout); a very slow site holds a worker thread for that long.

## Platform
- **Login rate limiter is per process** (with several API replicas each enforces its own limit).
- **S3 storage** is supported for AWS but not exercised by automated tests (no AWS in CI).
- **No license file yet.** A license will be added.
