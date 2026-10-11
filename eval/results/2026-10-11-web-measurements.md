# Web measurements: the React site (Phase 5, step 5g)

- Measured 2026-10-11 in Safari on an M2 MacBook Air (8 GB), through the built site (`npm run preview`, its content security policy on), the Node API, the Python service, MongoDB Atlas, and Gemini
- Build: commit `39fc1b9` (the timing points in `55d9def`, the dagre layout in `39fc1b9`); main bundle `index-DOsJvSqw.js`
- Browser numbers come from the browser's own timing records (Navigation, Paint, Resource, and User Timing), read by a console snippet after each step; the service's stage times come from each reply's `timings_ms`
- Everything ran on one laptop, so network delay is near zero: page times measure the machine, not the internet. Download sizes do not depend on that. Every row is one run unless it says otherwise
- Quota: 5 answer requests, 0 embedding requests (all five questions' embeddings came from the cache)

## Download sizes

| Page | Files | Transferred (gzip, as served) | Unpacked |
| --- | --- | --- | --- |
| Every page: HTML, CSS, JavaScript | 3 | 98 kB (CSS 2.1 kB, JS 95.8 kB) | 315 kB |
| Overview page, added for the diagram (Mermaid with dagre) | 29 | 227 kB | 814 kB |
| Overview page, added for the diagram (Mermaid with ELK, before the fix) | 30 | 663 kB (ELK alone 450 kB) | 2,234 kB |

Mermaid loads only on the overview page. Mermaid 12 lays out flowcharts with ELK by default; 5f's estimate of about 210 kB assumed dagre, and the browser showed ELK being downloaded. Setting `layout: "dagre"` replaced the 450 kB ELK file with a 17 kB dagre file. The site is a single bundle with no per-page splitting other than Mermaid. Brotli at its highest level, computed from the built files, would send the every-page files as 84 kB instead of 97 kB.

## Page loads

| Page | Cache | Page ready (DOMContentLoaded) | First content on screen | Transferred |
| --- | --- | --- | --- | --- |
| Home | empty | 48 ms | 58 ms | 98 kB |
| Home | warm | 43 ms | 52 ms | 0.6 kB (two "not modified" replies) |
| Overview, ELK | empty | 385 ms | 389 ms | 761 kB |
| Overview, dagre | empty | 40 ms | 43 ms | 325 kB |

The preview server asks the browser to check each file again on every visit, so a warm load still makes one small request per file. The 385 ms of the ELK run is likely a first-load delay unrelated to ELK (ELK loads after the page is ready); one sample each.

## Diagram

| Layout | Load Mermaid | Draw | Diagram shown (from the start of drawing) | Diagram shown (from the start of the page load) |
| --- | --- | --- | --- | --- |
| ELK | 36 ms | 374 ms | 429 ms | 1,209 ms |
| dagre | 50 ms | 73 ms | 130 ms | 618 ms |

Draw includes downloading and unpacking the layout engine on first use. Diagram: Flask 3.1.3, the 12 most connected files.

## Time to a shown answer

Five Flask 3.1.3 evaluation questions (flask-003 to flask-007), asked in order in one new conversation, test files included.

| Question | Shown in the browser | Request to Node | Service stages | Of which the answer model | Node, proxy, and untimed service work | Browser after the reply |
| --- | --- | --- | --- | --- | --- | --- |
| flask-003 | 3,975 ms | 3,614 ms | 3,198 ms | 2,224 ms | 416 ms | 361 ms |
| flask-004 | 3,690 ms | 3,651 ms | 3,237 ms | 3,114 ms | 414 ms | 39 ms |
| flask-005 | 2,423 ms | 2,394 ms | 2,013 ms | 1,769 ms | 381 ms | 29 ms |
| flask-006 | 2,804 ms | 2,758 ms | 2,370 ms | 2,229 ms | 388 ms | 46 ms |
| flask-007 | 2,485 ms | 2,442 ms | 2,064 ms | 1,924 ms | 378 ms | 43 ms |
| Median | 2,804 ms | 2,758 ms | 2,370 ms | 2,224 ms | 388 ms | 43 ms |

Shown in the browser: from pressing Ask until the answer is on the page. Request to Node: the browser's record of the question's request. Service stages: the sum of the reply's `timings_ms` (checks, embed query, vector search, generate answer). The answer model took 73% of the total time across the five (11,260 of 15,377 ms). The first question's extra browser time is the new conversation being created and then opened (two more requests); its 868 ms checks are the service's first index check after starting, remembered for 5 minutes afterwards (Phase 3f). Node's share here (378 to 416 ms) is larger than 4g's measured 275 ms of Atlas commands for a question; it also includes the development proxy and the service's untimed work, which this measurement cannot separate.
