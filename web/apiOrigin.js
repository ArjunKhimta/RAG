/**
 * The public addresses of the two servers on Render. The site's `/api` forwarding in
 * `vercel.json`, the content security policy, and the page's wake-up calls use them; tests fail if
 * they disagree with each other or with `render.yaml`.
 */
export const API_ORIGIN = "https://repositorycite-api.onrender.com";

export const RETRIEVAL_SERVICE_ORIGIN = "https://repositorycite-retrieval.onrender.com";
