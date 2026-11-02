# Operations runbook

## Health
`GET /api/health/live` (process) · `GET /api/health/ready` (database + active AI mode) · `GET /api/v1/system/info` (provider and model shown in the UI).

## Processing problems
- **A document stays `queued`.** The worker isn't running. Run `docker compose ps worker` and check its logs.
- **A document is `failed`.** The detail page shows the failing stage and error, and **Administration → Recent jobs** shows attempts. Common causes: corrupt or password-protected PDFs (stage `extract`), or more than `RKE_MAX_PAGES` pages. Re-uploading creates a new version that is processed fresh.
- **Re-process everything** (e.g. after changing the chunking or embedding model). Enqueue `process_version` for all versions; processing is idempotent, so old chunks are replaced, not duplicated:
  ```bash
  docker compose run --rm api python -c "from sqlalchemy import select; from app.db.session import session_scope; from app.db.models import DocumentVersion; from app.jobs import queue; s=session_scope().__enter__(); [queue.enqueue(s,'process_version',{'version_id':str(v.id)}) for v in s.scalars(select(DocumentVersion))]; s.commit()"
  ```
- **Slow processing.** Check thread limits (`RKE_EMBEDDING_THREADS`, `OMP_THREAD_LIMIT`). Oversubscription made processing about 10× slower in testing.

## Answers look wrong
1. Open the citation. Is the cited passage right? If not, it's a retrieval issue: run **Search** with the question and compare the order with and without re-ranking, and the relevance scores.
2. "Not found" with a note like *No accessible document mentions: X*: the entity filter found no source containing X. Check the spelling, and whether the user is a member of the workspace that holds it.
3. "Not found" without a note: the best passage's re-ranker relevance was below `RKE_MIN_RERANK_RELEVANCE` (0.2). Lowering it trades fewer refusals for more answers from weak evidence.
4. "The sources disagree": the cited passages give different quantities. Check the freshness badges; an outdated source may need archiving.
5. Re-run `make evaluate` after changes to make sure leakage stays 0 and refusals stay at 100%.

## Access questions
"Why can user X see source Y?" Access comes only from workspace membership. Look up the access view:
```sql
SELECT * FROM readable_documents WHERE document_id = '<id>';
```
Each row is a workspace member: `owner`, `edit` (editor) or `read` (reader). Membership changes are in `audit_log` (`workspace.member`, `workspace.member_remove`).

## Web sources
- Re-fetch one source from its page, or all web sources of a knowledge base with `POST /api/v1/knowledge-bases/{id}/refresh` (background job). Unchanged pages are not re-processed.
- Restrict which sites may be added with `RKE_URL_ALLOWLIST='["docs.example.com"]'`. Private, internal and cloud-metadata addresses are always refused.

## Backups
- **Local:**
  ```bash
  docker compose exec db pg_dump -U rke -Fc rke > rke.dump
  ```
  Also back up the `files` volume (the original uploads).
- **AWS:** RDS automated backups (7 days) and a final snapshot, plus the S3 bucket's versioning (7-day window for deleted files).

## Changing the AI provider
- **Turn Claude on:** set `ANTHROPIC_API_KEY`, then restart the API and worker. New uploads get Claude summaries, classification and extraction; existing documents keep their rule-based output until re-processed.
- **Turn Claude off:** remove the key, or set `RKE_LLM_PROVIDER=extractive`.
- **Cost control:** use `RKE_CLAUDE_MODEL` and `RKE_CLAUDE_EFFORT` (default `medium`). Each answer stores token usage in `messages.usage`.
