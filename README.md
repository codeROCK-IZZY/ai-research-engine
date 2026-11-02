# AI Research & Knowledge Engine

A knowledge platform for teams: build shared knowledge bases from files and web
pages, search them with hybrid keyword + semantic retrieval and re-ranking, and get answers that cite their
sources and say "not enough information" instead of guessing.

> **Status: in development.** Architecture and decisions: [docs/DESIGN.md](docs/DESIGN.md).

## The problem

Company knowledge is scattered across wikis, PDFs, policies, research notes and web pages. Keyword search
misses documents that use different words, and AI assistants that are not grounded in the organisation's
own sources invent answers. Teams lose hours searching and act on outdated or contradictory information.

## What this engine does (in development)

- Shared knowledge bases for each team, with workspace permissions.
- Ingests files and web pages, keeps them up to date and spots duplicate content.
- Finds the right passages even when the question uses different wording (hybrid search with re-ranking).
- Answers questions with citations, points out when sources disagree, and says so when the knowledge base cannot support an answer.
- Measures search quality and answer quality separately, so improvements are proven rather than assumed.

**Who it is for:** research, support and operations teams that rely on a large, changing body of documents.

**Stack:** Python · FastAPI · PostgreSQL + pgvector · ONNX embeddings and re-ranker · Claude (optional) ·
React + TypeScript · Docker · Terraform (AWS, not applied)

