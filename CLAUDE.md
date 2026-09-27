# PROJECT_NAME

## What this project is
A full-stack code search engine. Users sign in with GitHub, import a repository, and ask questions in plain English. Answers cite exact files and line numbers. Python repositories only for now.

## Architecture
- `web/` React (Vite) frontend, deployed on Vercel
- `api/` Node.js + Express: GitHub OAuth + JWT, users, repos, indexing jobs, chat history, rate limiting, ownership checks
- `retrieval/` Flask service: Tree-sitter chunking, embeddings, hybrid search, reranking, answer generation
- `eval/` Evaluation: 50 handwritten questions, SWE-bench Lite subset, Ragas
- Database: MongoDB Atlas (vector index + Atlas Search BM25 index)
- Models: Gemini API (default), Ollama (local private mode), Cohere or a local cross-encoder for reranking

## Retrieval pipeline
1. Parse with Tree-sitter: one chunk per function or class, with file path, line range, name, and parent class
2. Embed in batches; cache embeddings by content hash
3. Query router: exact identifiers go to keyword search; natural-language questions go to hybrid search
4. Hybrid search: BM25 + vector, merged with Reciprocal Rank Fusion
5. Rerank the top 30 results and keep the best 5
6. Expand context with the code graph (callers and callees via `$graphLookup`)
7. Generate the answer with file and line citations
8. Record timing for every stage

## Incremental indexing
- Merkle tree of file and folder hashes; skip unchanged folders entirely
- Re-index only changed files; delete chunks from removed files
- Invalidate cached answers that cited changed files

## Current phase
Phase 1: search engine as plain Python scripts in `retrieval/`, run from the terminal. No web layer yet.

## Rules
- Never execute code from cloned repositories; only read it
- Never read, print, or edit `.env` files; reference variables by name only
- Never hard-code keys or secrets
- Evaluation numbers must come from real runs; never estimate, invent, or fill in results
- Only accept GitHub URLs and enforce a maximum repo size
- Respect free-tier limits: Render 512 MB RAM, Atlas 512 MB storage, Gemini free-tier rate limits
- Development machine is an M2 MacBook Air with 8 GB RAM and limited disk space; prefer lightweight local models and avoid large downloads without asking

## Code style
- Descriptive variable names, one idea per line, no compressed or clever shorthand
- No inline comments; code should explain itself through naming and small functions
- Type hints in Python
- Tests with pytest for `retrieval/` and `eval/`, and Jest for `api/`

## How to work with me
- Propose a plan before writing code for any non-trivial task, and wait for my approval
- One feature per task; keep changes small and reviewable
- For core pieces (chunker, rank fusion, reranking, Merkle tree, cache invalidation, evaluation), explain the approach and trade-offs in plain terms before implementing, so I can explain them in interviews
- After each change, tell me which tests to run and suggest a commit message
- Update the "Current phase" section when a phase is complete

## Roadmap
1. Search engine as Python scripts
2. Evaluation
3. Flask API
4. Node API and MongoDB
5. React frontend
6. Incremental re-indexing
7. Docker, CI/CD, deployment
8. Polish

## Commands
- Activate environment: `source retrieval/.venv/bin/activate`
- Run tests: `pytest retrieval/tests`

## Environment variables
- `GEMINI_API_KEY`
- `MONGODB_URI`

## Paths
- Cloned repositories go in `data/repos/`, which is gitignored
- Demo repository: pallets/flask

## Out of scope
- Languages other than Python until Phase 8
- Pull request review, IDE extensions, cross-repo search
