# AI card — how answers are produced

## Pipeline per question
1. **Follow-up resolution.** "and who owns it?" borrows the key terms of the previous question. Offline this happens only when the question refers back ("it", "that", "and…"); Claude rewrites the question.
2. **Entity filter.** Codes and capitalised names in the question ("INC-2030", "Ledger Service") must appear in a source for it to be eligible. Ordinals such as "95th" are not names. If no source qualifies, the answer is "not found" and no model is called.
3. **Hybrid retrieval** over the knowledge bases the user can read (workspace membership, enforced inside the SQL): PostgreSQL full text plus pgvector cosine similarity, fused with Reciprocal Rank Fusion.
4. **Re-ranking.** `Xenova/ms-marco-MiniLM-L-6-v2`, a cross-encoder running locally on CPU, scores the question against each of the top 15 passages; the best 8 are kept.
5. **Relevance gate.** If the best passage's re-ranker relevance is below 0.2, the answer is "not found" without calling a model. (In the benchmark, answerable questions scored at least 0.37 and off-topic ones about 0.0.)
6. **Answer:**
   - *Offline (extractive, default):* the one or two source sentences that best answer the question are quoted verbatim, each cited. Headings are never quoted; questions asking for a value prefer sentences with numbers. No generated text.
   - *Claude (when `ANTHROPIC_API_KEY` is set):* passages are sent as separate document blocks with native citations; the system prompt marks retrieved text as untrusted data and asks for "NOT_FOUND" when unsupported.
7. **Grounding enforcement in code.** An answer with no citations is withheld; uncited sentences are highlighted as unsupported.
8. **Cross-source check (rule-based).** Quantities following the same question term in passages from different sources are compared; differences are reported as a conflict, naming both sources.

## Models
| Purpose | Model | Where it runs |
|---|---|---|
| Embeddings | `BAAI/bge-small-en-v1.5` (384-d) | Local CPU (ONNX), baked into the image |
| Re-ranking | `Xenova/ms-marco-MiniLM-L-6-v2` | Local CPU (ONNX), baked into the image |
| Answers (optional) | Claude via the Anthropic API | Anthropic |

## Evaluated how
See [eval/reports/evaluation.md](../eval/reports/evaluation.md): 77 labelled questions over 37 synthetic sources, including paraphrases, a deliberate contradiction between an old and a current policy, unanswerable questions, and a question about a knowledge base the asker cannot read. Retrieval is scored separately from answers. A regression guard in CI fails on any access leak, on lost refusals, or on degraded retrieval.

## When not to trust it
- **Paraphrased questions in offline mode.** Quoting a sentence works when the wording overlaps; heavy paraphrases are answered correctly about half the time in the benchmark.
- **Disagreements that are not numbers** ("allowed" vs "not allowed") are not detected; read both cited sources.
- **Stale sources.** Check the freshness badge; old documents can still be the best keyword match.
- **Decisions with legal or financial consequences.** Use the citation to check the original.

## Data handling
- **Offline mode:** nothing leaves the server.
- **Claude mode:** the question, recent conversation turns and the retrieved passages (not whole documents) are sent to Anthropic's API per question.
- **Web sources:** only the single requested page is fetched, after SSRF checks.
