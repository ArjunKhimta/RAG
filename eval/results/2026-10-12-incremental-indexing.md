# Incremental re-indexing measurements (Phase 6, step 6f)

- Measured 2026-10-12 on an M2 MacBook Air (8 GB) against MongoDB Atlas (free tier) and GitHub, at commit `2981e11` (6a to 6e); measurement scripts were scratch files, not committed
- Repository: pallets/flask, from the local checkouts of tags 3.1.3 (`22d9247`, 83 Python files, 1,009 chunks) and 3.1.2 (`2c1b30d`, 1,011 chunks); 7 Python files differ between them (`examples/tutorial/flaskr/__init__.py`, `src/flask/app.py`, `ctx.py`, `sansio/app.py`, `sessions.py`, `templating.py`, `tests/test_basic.py`)
- Each indexing run is the real `index_repository_version` (the same code the service's jobs and the index script run), under a throwaway version name, in its own Python process (so peak memory is per run), with every Atlas command counted by the MongoDB driver's command monitoring; 3 runs per scenario, medians with ranges
- A second throwaway version stayed at 3.1.2 throughout so its chunks stayed in the embedding cache: every measured run made 0 embedding requests; seeding that version made the only ones (21 texts in 1 request)
- Everything ran from one laptop to Atlas over the internet: Atlas round trips dominate, and on a server in Atlas's region they would be shorter. Times are not comparable with other machines
- Quota: 21 embedding requests (approved), 0 answer requests

## Indexing a version again

| Scenario | Mode | Files changed | Chunks written / kept / deleted | Embed and store | Whole run | Atlas commands (time) | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Full re-index (fingerprints removed; every embedding from the cache) | full | (not compared) | 1,009 / 0 / 0 | 11,172 ms (9,566 to 11,513) | 15,170 ms (13,181 to 15,552) | 16 (13,207 ms) | 154.8 MB |
| No change (same commit again) | incremental | 0 of 83 | 0 / 1,009 / 0 | 235 ms (210 to 263) | 3,461 ms (3,317 to 3,987) | 15 (1,622 ms) | 91.8 MB |
| 3.1.3 to 3.1.2 | incremental | 7 of 83 | 249 / 762 / 127 | 4,068 ms (4,005 to 4,313) | 7,360 ms (7,230 to 7,657) | 19 (5,461 ms) | 103.8 MB |
| 3.1.2 to 3.1.3 | incremental | 7 of 83 | 247 / 762 / 129 | 4,486 ms (4,156 to 6,311) | 8,746 ms (7,848 to 9,490) | 19 (6,779 ms) | 102.9 MB |

Every run made 0 embedding requests. Each change between 3.1.2 and 3.1.3 also updated 1 call link in a file that did not change (the call graph crosses files), as in 6c; full and no-change runs updated none. Clone time is 0 here (local checkouts); a real refresh job also clones, 2.4 s for Flask in 6d.

### Where the time goes

| Stage (median ms) | Full | No change | 3.1.3 to 3.1.2 | 3.1.2 to 3.1.3 |
| --- | --- | --- | --- | --- |
| Walk, scan for secrets, chunk | 1,835 | 1,821 | 1,840 | 1,844 |
| Call graph | 6 | 6 | 6 | 6 |
| Compare with stored index (record, fingerprints, every chunk's ID and calls) | 70 | 342 | 314 | 289 |
| Embed and store | 11,172 | 235 | 4,068 | 4,486 |
| Store fingerprints | 43 | 55 | 43 | 50 |
| Untimed: making sure the store and search indexes exist | 2,054 (1,649 to 2,113) | 1,035 (877 to 1,476) | 981 (846 to 1,461) | 1,520 (934 to 1,991) |

The untimed row is each run's whole time minus its timed stages; by Atlas command it is 4 `createIndexes` (about 250 ms together) and 2 `aggregate` calls listing the search indexes (491 to 1,662 ms per run, varying run to run as the index status checks did in 3f).

Atlas commands, median time per scenario:

| Command | Full | No change | 3.1.3 to 3.1.2 | 3.1.2 to 3.1.3 |
| --- | --- | --- | --- | --- |
| `getMore` (reading results in batches: cached vectors, chunk IDs and calls) | 1 call, 5,265 ms | 1, 189 ms | 2, 1,567 ms | 2, 1,884 ms |
| `update` (chunk writes, call links, the commit, the record, the fingerprints) | 3, 5,293 ms | 3, 150 ms | 5, 2,337 ms | 5, 2,118 ms |
| `find` | 4, 571 ms | 3, 154 ms | 4, 441 ms | 4, 507 ms |
| `aggregate` (search index status) | 2, 1,637 ms | 2, 622 ms | 2, 534 ms | 2, 1,150 ms |
| `createIndexes` | 4, 257 ms | 4, 259 ms | 4, 244 ms | 4, 248 ms |
| `delete` (stale chunks) | 1, 54 ms | 1, 139 ms | 1, 166 ms | 1, 116 ms |
| `collStats` | 1, 190 ms | 1, 142 ms | 1, 145 ms | 1, 121 ms |

A full re-index spends its time moving vectors: reading back 1,009 cached vectors (about 3 KB each) and writing 1,009 chunks with their vectors. A patch reads and writes vectors only for the chunks of changed files.

### Read

- No change: the whole run fell from 15.2 s to 3.5 s (4.4 times faster) and storing from 11.2 s to 0.2 s; what is left is chunking every file (1.8 s, needed for the call graph) and the index checks (about 1 s)
- 7 of 83 files changed: the whole run fell to 7.4 to 8.7 s (medians; 1.7 to 2.1 times faster). The saving is smaller than the 7 files suggest, because those files hold a quarter of the chunks (`src/flask/app.py` and `tests/test_basic.py` are the two largest), every chunk of a changed file is rewritten, and a line shifted inside a changed file gives the chunks below it new IDs
- Peak memory fell from 155 MB to 92 MB (no change) and 103 MB (7 files), since a patch never holds every vector in memory
- Not reduced: cloning, scanning, and chunking every file, and checking the indexes. The index checks could be skipped for a version already indexed (a possible later change)

## The refresh check

| What | Runs | Median | Range |
| --- | --- | --- | --- |
| `git ls-remote` for the tag 3.1.3 (from this machine to GitHub) | 5 | 1,009 ms | 867 to 1,139 ms |
| `git ls-remote` for the branch `main` | 5 | 873 ms | 838 to 996 ms |
| Refresh button, tag still at its commit: 200 up to date through the built site, Node, the service | 5 | 1,244 ms | 1,155 to 1,264 ms |
| Refresh button, a commit-ID version: 200 up to date, no GitHub call | 5 | 277 ms | 249 to 296 ms |
| Reading a ready link, for comparison | 5 | 116 ms | 65 to 142 ms |

No indexing slot was used by any of the 10 refreshes. Nearly all of the up-to-date reply is the GitHub check. A refresh that found a moved branch (6d, one sample, Flask `stable` from 3.1.3's commit to `81798b4`, 10 files changed, 16 embeddings) took 1,338 ms to answer 202 and 17 s until the link showed the new commit, including a fresh clone (2.4 s), 6.8 s of embedding and storing, and the site's 3-second polling.

## Follow-up 2026-10-13: the index check alongside chunking (Phase 8, step 8d)

The untimed index check above (about 1 to 2 s per run) now starts on its own thread as soon as the clone is ready and runs while the files are walked, scanned, and chunked; the run waits for it just before comparing with the stored index. It still creates a missing index and updates a changed definition on every run, and its failure still stops the run before anything is stored. It is now timed as two stages: the check itself, and the wait for it.

Measured differently from the tables above: the whole `index_repository.py` process (`/usr/bin/time`, so Python start-up, the Atlas connection, and the closing statistics are included), re-indexing Flask 3.1.3 with nothing changed from its saved clone, 5 runs each, 0 embedding requests.

| | Runs (s) | Median |
| --- | --- | --- |
| Before (commit `3341d00`) | 4.49, 4.57, 5.41, 5.52, 5.62 | 5.41 s |
| After | 3.76, 3.81, 3.93, 4.04, 4.18 | **3.93 s** (−1.48 s, −27%) |

After the change, the check itself took 1,431 to 2,237 ms and the run waited 0 to 415 ms for it, since chunking took 1,808 to 1,842 ms. On Render, where chunking Flask took 34.9 s (7f), the check is hidden entirely.
