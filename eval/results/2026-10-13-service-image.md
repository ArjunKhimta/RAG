# Service image without the reranker (Phase 8, step 8d)

- Measured 2026-10-13 on an M2 MacBook Air (8 GB): the laptop's Python 3.12 virtual environment, and the retrieval service's Docker image built with Colima (Docker Engine 29.5.2, a 2-CPU, 2 GB VM)
- Before: commit `8c16abd` (in a temporary worktree); after: the same code with the reranker split out (`retrieval/src/retrieval/cross_encoder.py`, imported only when a reranker is built, and `requirements-reranker.txt` kept out of the image's lock)
- The service never reranks (answers and searches use vector search; Phase 2 measured reranking as no better), but it imported ONNX Runtime, tokenizers, and numpy at start-up, and its image installed them
- Quota: 2 answer requests (one question per image), 0 embedding requests

## Image size

| Image | Compressed (what the host downloads) | Unpacked, whole image | Python packages |
| --- | --- | --- | --- |
| x86-64 (Render), before | 150 MB | 478 MB | 260 MB |
| x86-64 (Render), after | **95 MB** (−55 MB, −37%) | **305 MB** (−173 MB, −36%) | **87 MB** (−67%) |
| arm64 (laptop), before | 148 MB | 494 MB | 250 MB |
| arm64 (laptop), after | 94 MB | 332 MB | 87 MB |

The image's lock went from 51 packages to 40: ONNX Runtime, numpy, tokenizers, and the 8 packages only they need (huggingface-hub, hf-xet, fsspec, filelock, protobuf, flatbuffers, packaging, tqdm). Compressed sizes are from `docker image inspect`, unpacked sizes from `du` inside each image. The x86-64 images were built by emulation; their contents are the same as Render's build from the same files.

## Memory and start-up

| | Before | After |
| --- | --- | --- |
| Laptop: importing the service, peak memory (6 alternating runs) | 107.0 to 108.8 MB | **80.4 to 80.8 MB** |
| Laptop: importing the service, time (6 alternating runs) | 395 to 501 ms (median about 410) | 355 to 378 ms (median about 357) |
| Container: memory when `/health` first answers (4 runs) | 98.7 to 103.9 MB | **83.4 to 83.6 MB** |
| Container: peak after one search and one question (1 run) | 107.8 MB | **87.3 MB** |
| Container: from `docker run` to `/health` answering (4 runs) | 1,462 to 1,769 ms | 1,423 to 1,483 ms |

Memory is cgroup `memory.current` and `memory.peak` inside the container (which count file cache too), and `ru_maxrss` on the laptop. Start-up to a healthy reply did not change measurably: gunicorn's start, the app's settings, and the first Atlas connection dominate it, not the imports. Render's free plan shows neither memory nor start-up time, so these are local measurements.

Both images answered a search (`get_signing_serializer` ranked first, about 910 ms each, the first index check) and the session-cookie question (found, 3 sentences, 1 answer request, embedding from the cache) with no errors logged.

## What did not change

The scripts and the evaluation still rerank: `requirements-dev.txt` includes `requirements-reranker.txt`, and CI installs that lock. A process that asks for a reranker without those packages gets `RerankerLibrariesMissingError` naming the file to install. A test starts a fresh Python, imports every module the service runs, and fails if ONNX Runtime, tokenizers, numpy, or `retrieval.cross_encoder` were loaded; adding a numpy import to `reranking.py` on purpose made it fail.
