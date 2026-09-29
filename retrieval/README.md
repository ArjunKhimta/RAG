# retrieval

Chunking, embeddings, hybrid search, reranking, and answer generation. Phase 1 runs as plain
Python scripts from the terminal; the Flask layer arrives in Phase 3.

## Setup

```bash
python3.12 -m venv retrieval/.venv
source retrieval/.venv/bin/activate
pip install -r retrieval/requirements-dev.txt
pip install -e retrieval
```

`pip install -e retrieval` puts the `retrieval` package on the path so `from retrieval.config
import ...` works from scripts and tests. Dependencies are pinned in `requirements.txt` rather
than in `pyproject.toml`, so there is one source of truth for versions.

`tree-sitter` is pinned to 0.25.2 because 0.26.0 segfaults when reading line positions from
large trees. `tests/test_parsing.py` reproduces the crash, so run it before upgrading.

Copy `.env.example` to `.env` and fill in `MONGODB_URI` and `GEMINI_API_KEY`.

## Layout

```
src/retrieval/
    config.py              environment variables and model names
    redaction.py           scrubs secrets out of anything about to be displayed
    clients.py             MongoDB and Gemini client constructors
    connection_checks.py   reachability checks for both services
    parsing.py             shared Tree-sitter parser; the one place rows become 1-indexed lines
    chunker.py             splits a Python file into function, method, class, and module chunks
    github_urls.py         accepts only https://github.com/<owner>/<repo> URLs
    licenses.py            allowed licenses: OSI-approved SPDX IDs plus CC0-1.0
    repository_cloner.py   public, licensed, size-checked shallow clones into data/repos/
scripts/
    check_connections.py   command line entry point for those checks
tests/
```

## Checking connectivity

```bash
python retrieval/scripts/check_connections.py
```

Prints one line per service with a pass/fail status, elapsed time, and a detail message. Exits 0
when both succeed, 1 otherwise. Credentials never appear in the output: every detail line passes
through `redaction.redact`, which removes registered secret values and strips `user:password@`
out of any URI. This matters because driver and HTTP errors routinely quote the connection URI
or the API key back at you.

## Tests

```bash
pytest retrieval/tests                  # unit tests, offline, no credentials needed
pytest retrieval/tests -m integration   # reaches the real services
```

The default run excludes the `integration` marker, so tests never depend on the network or on a
populated `.env`.
