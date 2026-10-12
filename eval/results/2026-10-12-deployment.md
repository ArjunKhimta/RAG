# Deployment measurements: the live site (Phase 7, step 7g)

- Measured 2026-10-12 on the live deployment: the site on Vercel (`repositorycite.vercel.app`), the Node API and the Python service on Render's free plan in Singapore (`repositorycite-api.onrender.com`, `repositorycite-retrieval.onrender.com`), MongoDB Atlas in AWS `ap-south-1` (Mumbai), Gemini
- Deployed code: commit `b85c32c` (the starting-up message and its direct wake-up call); `e39431d` changed only `CLAUDE.md`
- Client: Arjun's M2 MacBook Air (8 GB) in India, Safari for the browser rows and a Python script (httpx) for the rest; Vercel served from its Mumbai edge (`bom1`)
- Browser numbers come from Safari's own timing records (Navigation, Paint, Resource, and User Timing, the marks added in 5g), read by a console snippet after each step; the service's stage times come from each saved reply's `timings_ms`
- Compared with `2026-10-11-web-measurements.md` (5g: everything on the laptop, so near-zero network delay) and 4g's Node timings (laptop to Atlas)
- Quota: 5 answer requests, 0 embedding requests (the five questions' embeddings and the search came from the cache)
- Every row is one run unless it says otherwise

## Waking from sleep

Render's free plan stops a service after 15 minutes without requests. Both runs below waited 17 or 18 minutes with nothing sent to either service, then started every request at once from a script on the laptop and repeated a refused one every 3 s, as the site does.

**Run 1 (16:30 UTC): requests through Vercel only.** Every `GET /api/me` through the site was refused at once with 502 and Render's header `x-render-routing: no-deploy`: 54 attempts over 181 s, none waking the API. Three minutes in, the script's first direct request to the API's own address woke it, and the site's path answered 401 about 40 s after that (16:34:04). So a request forwarded by Vercel does not wake a sleeping Render service, and a request to the service's own address does; without the site's direct wake-up call (`wakeServer`, `b85c32c`), a visitor would wait until someone else woke the API. The retrieval service was awake in this run (`/health` in 251 ms), for reasons not known (nothing in this project had called it for 17 minutes).

**Run 2 (17:11 UTC): as the site does it.** The direct call to the API's `/health` (the site's wake-up call) and `GET /api/me` through the site, at the same moment, plus the service's `/health` directly to time its own wake-up:

| Request | Answer | Time from the start |
| --- | --- | --- |
| API `/health` directly (the wake-up call) | 200 after being held 12.6 s | 12,570 ms |
| Site `/api/me`, first try | 502 `no-deploy` in 728 ms | |
| Site `/api/me`, second try (3.7 s in) | 401 after being held 9.4 s | **13,097 ms** |
| Service `/health` directly | 200 after being held 23.5 s | 23,531 ms |

Once the API had started waking, Render held the site's next request until the API was ready instead of refusing it. A visitor arriving while both sleep sees the starting-up message for about **13 s**, then the page (one run; 7f's single unclean sample was 13.5 s for the API and 23.7 s for the service). The service takes longer, about 23.5 s here. On the site nothing calls the service directly: the API calls its `/health` once as it starts (`wakeRetrievalService`, 7f-1), so for a visitor the service would be ready about 12.6 + 23.5, roughly 36 s after the first visit if its wake-up takes as long when started that way (not measured). A question or search sent before then waits inside Render's hold, within Node's 90 s (answers) and 15 s (searches) limits; a search in that window could time out with 503 and `Retry-After: 30`. The first question after a wake-up also pays the service's first index check (about 0.9 s, below).

**Found afterwards (the same evening): the API cannot wake the service.** After both had slept again, a question on the live site failed with "The answer failed its citation check". The service's log showed it never started; the API's log showed its start-up wake-up call refused after 326 ms (at 17:11 it had been held 12.3 s only because this script was waking the service directly at the same moment). Render refuses a request from another server to a sleeping service, as it refuses Vercel's, and Node read Render's 502 as the service's own 502 for a rejected answer. So the estimate above, the service ready about 36 s after the first visit, was wrong: without a direct call it never woke. Fixed the same day: the site calls both servers' health checks directly on every page load and whenever a request finds a server asleep, and Node turns a 502, 503, or 504 that is not the service's own JSON reply into 503 "starting up" with `Retry-After: 30`, giving the person's slot back.

## Distance to the database

Ten rounds from Safari through the site, each round one request of each kind; medians, with the range.

| Request | Atlas commands | Time | Added over the unsigned request |
| --- | --- | --- | --- |
| `GET /api/me`, no session cookie (answered without the database) | 0 | 139 ms (109 to 410) | |
| `GET /api/me`, signed in (the session check) | 1 | 191 ms (183 to 218) | 52 ms |
| `GET /api/repositories` | 2 | 305 ms (254 to 376) | 166 ms, about 83 per command |
| `GET /api/me/usage` | 3 | 340 ms (322 to 364) | 201 ms, about 67 per command |

From the laptop, each Atlas command cost about 29 to 30 ms (4g). From Render in Singapore to Atlas in Mumbai it costs about 52 to 83 ms. The unsigned request's 139 ms is the path itself: Safari to Vercel's Mumbai edge, Vercel to Render (through Cloudflare), Express, and back.

From the laptop with a script (12 requests on one connection after a warm-up; medians): the site's page 38 ms, `/api/me` without a session through Vercel 113 ms, the API's `/health` directly 68 ms, the service's `/health` directly 72 ms.

## Time to a shown answer

The same five Flask 3.1.3 evaluation questions as 5g (flask-003 to flask-007), asked in order in one new conversation, test files included.

| Question | Shown in the browser | Request to Node | Service stages | Of which the answer model | Node, network, and untimed service work | Browser after the reply |
| --- | --- | --- | --- | --- | --- | --- |
| flask-003 | 4,925 ms | 3,777 ms | 2,784 ms | 1,747 ms | 993 ms | 1,148 ms |
| flask-004 | 2,368 ms | 2,343 ms | 1,430 ms | 1,233 ms | 913 ms | 25 ms |
| flask-005 | 2,581 ms | 2,554 ms | 1,682 ms | 1,474 ms | 872 ms | 27 ms |
| flask-006 | 2,475 ms | 2,451 ms | 1,617 ms | 1,418 ms | 834 ms | 24 ms |
| flask-007 | 2,431 ms | 2,412 ms | 1,495 ms | 1,297 ms | 917 ms | 19 ms |
| Median | 2,475 ms | 2,451 ms | 1,617 ms | 1,418 ms | 913 ms | 25 ms |
| Median, local (5g) | 2,804 ms | 2,758 ms | 2,370 ms | 2,224 ms | 388 ms | 43 ms |

Service stages, live medians against 5g's: checks 62 against 32 ms, embedding the question (from the cache) 62 against 37 ms, vector search 74 against 73 ms. Each check and cache read is one Atlas round trip, so they doubled; the vector search did not change. The first question's checks took 896 ms: the service's first index check after starting (remembered for 5 minutes, Phase 3f). Its browser time includes creating the conversation (504 ms) before the question and opening it (604 ms) after.

Live answers came faster than local ones because the answer model did (1,418 against 2,224 ms median; Gemini's speed varies between runs, as 4g found). The part the deployment adds, Node, the network hops, and the service's untimed work, is 913 ms against 388 ms: a question makes about nine Atlas commands in Node (4g) at 52 to 83 ms each, plus the hops from Vercel to Render and from the API to the service through its public address.

Other routes through the site (single requests unless a range is given): search with the default 10 results 838 ms from pressing Search to the reply (235 ms locally through Node, 4g); reading a ready repository link 267 to 352 ms (61 ms locally); the repository list 321 and 351 ms; reopening the conversation 544 ms (92 ms locally).

## Page loads

| Page | Cache | Page ready (DOMContentLoaded) | First content on screen | Files transferred | Local (5g) |
| --- | --- | --- | --- | --- | --- |
| Home | empty | 190 ms | 206 ms | 2 files, 110.7 kB (347.8 kB unpacked) | 48 and 58 ms |
| Home | warm | 57 ms | 78 ms | 0 kB | 43 and 52 ms |
| Overview | empty | 401 ms | 685 ms | 31 files, 346.8 kB (1,161.7 kB unpacked) | 40 and 43 ms |

The HTML itself transferred 0.8 kB each time. On the warm load the browser used its stored files without asking Vercel at all, because `vercel.json` gives `assets/` a year-long immutable cache (7e); locally the preview server made the browser re-check each file. The home page's sign-in check (`/api/me`) took 224 to 227 ms after the page was ready.

Overview with an empty cache: the sign-in check 196 ms, the link 270 ms, then the overview data 808 ms (176 to 448 ms locally, 5f), loading Mermaid 130 ms, drawing 160 ms; the diagram was on screen 300 ms after drawing started and about 2,030 ms after the page load began (618 ms locally). The overview's 31 files are the site's 2 plus Mermaid's 29, as in 5g.

## Indexing on Render

Not repeated in 7g: the live check in 7f (Flask 3.1.3 at its commit ID, read back from its job record) is the measurement. Succeeded in 46 s: clone 1,975 ms, walk, scan, and chunk 34,914 ms (about 1.8 s on the laptop, so about 19 times slower on the free plan's 0.1 CPU), call graph 38 ms, comparing with the stored index 129 ms, embed and store 5,967 ms (1,009 chunks, all from the cache, 0 embedding requests), storing fingerprints 68 ms. Not measured: a repository near the 3,000-chunk cap; at the same rate its chunking alone would take about 100 s.

## Memory

Not measured on Render: the free plan does not show memory graphs. The closest measurement is 7c's, the same images on Linux in Docker (arm64, cgroup `memory.peak`, which counts file cache too): the service 123 MB through a search and a question and 196 MB during an indexing job (Flask, 1,009 chunks), the API 103 MB, against Render's 512 MB per service. Render's Events tab for both services listed no out-of-memory event, including around the 7f indexing job; Render restarts a service that goes over its limit and lists that there.

## Images and CI

Image sizes are 7c's local builds (arm64): the service 156 MB compressed, 506 MB unpacked; the API 86 MB compressed, 276 MB unpacked. Render's free plan builds its own linux/amd64 images and its build log was not read for sizes.

CI (GitHub Actions, `.github/workflows/ci.yml`): the 11 most recent runs (`844c7f6` to `e39431d`, read from GitHub's API) each took 44 to 48 s from push to finish, with no time queued; 10 succeeded, and the one failure (`8a8bde6`) was the loopback test fixed by `57ba28f`.

## For later

- The site's wake-up call could also call the service's `/health` directly, so both wake at once instead of one after the other (about 13 s sooner for the first question after a sleep, if the service's 23.5 s holds); the service's address would then appear in the site's code and content security policy
- Moving the database to Singapore (or the services to Mumbai, which Render does not offer) would remove most of the 0.5 s the Mumbai round trips add to each question and the 0.2 to 0.3 s they add to each page's data; not needed while answers show in about 2.5 s
- The overview waits 0.8 s for its data; it could be stored when a version is indexed instead of computed per request
