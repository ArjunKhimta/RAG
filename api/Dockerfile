# The Node API.
#
# Build from the api/ folder: docker build api
# The base image is pinned by digest, so every build starts from the same bytes; the tag says
# which release that digest is. `npm ci` installs exactly package-lock.json, whose integrity
# hashes npm checks, without development packages and without running install scripts; the
# registry signatures of what was installed are then verified. The API runs as the image's
# unprivileged `node` user and reads its settings from the environment; no .env file is copied.
FROM node:24.21.0-trixie-slim@sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN npm ci --omit=dev --ignore-scripts \
    && npm audit signatures \
    && npm cache clean --force

COPY src ./src

USER node

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:' + process.env.PORT + '/health').then((response) => process.exit(response.ok ? 0 : 1), () => process.exit(1))"]

CMD ["node", "src/server.js"]
