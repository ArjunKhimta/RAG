/**
 * Write `web/vercel.json` for a given Node API address (see `vercelConfig.js`):
 *
 *     node scripts/writeVercelConfig.js https://repositorycite-api.onrender.com
 *
 * Run it from `web/` whenever the API's address or `securityHeaders.js` changes, and commit the
 * result; the tests fail while the committed file is out of date.
 */

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { vercelConfig } from "../vercelConfig.js";

const OUTPUT_FILE = fileURLToPath(new URL("../vercel.json", import.meta.url));

const apiOrigin = process.argv[2];
if (!apiOrigin) {
  console.error("Usage: node scripts/writeVercelConfig.js <the Node API's https origin>");
  process.exit(1);
}
writeFileSync(OUTPUT_FILE, `${JSON.stringify(vercelConfig(apiOrigin), null, 2)}\n`);
console.log(`Wrote vercel.json forwarding /api to ${apiOrigin}`);
