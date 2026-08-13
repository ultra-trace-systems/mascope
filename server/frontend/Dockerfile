# This image is built from the REPO ROOT (see docker-compose.yaml
# frontend.build.context) so it can reach both server/frontend and the
# docs/ tree. Paths below are therefore relative to the repo root.

# FRONTEND BUILDER
FROM node:22-alpine AS frontend-build

ARG MASCOPE_RUNTIME

WORKDIR /app

# Bake the runtime name in before the copy so a build-context .env.production
# (a developer's local override) still wins, matching the previous behaviour.
RUN echo "MASCOPE_RUNTIME='${MASCOPE_RUNTIME}'" >> .env.production

# build frontend
COPY server/frontend/ ./
RUN npm install
RUN npm run build

# DOCS BUILDER
# Render the user documentation (docs/user) to static HTML. --strict fails the
# build on broken links, so a bad doc link breaks the image, not just CI.
FROM python:3.12-alpine AS docs-build

# Same uv version as CI (.github/workflows/tests.yaml).
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

WORKDIR /docs-src

# Install the exact docs toolchain from the lockfile (hash-checked), so the
# image builds the docs with the same mkdocs-material version CI validated.
# The root pyproject + lock are enough: the docs group has no workspace deps.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --only-group docs --no-emit-project -o requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

COPY mkdocs.yml ./
COPY docs ./docs
COPY tooling/docs/build_help_content.py ./
RUN mkdocs build --strict && \
    # emit help-content.json next to the site so nginx serves it at
    # /docs/help-content.json for the in-app help popovers
    python build_help_content.py

# RUNTIME
FROM nginx:1.27-alpine

# set timezone
ENV MASCOPE_TIMEZONE=Etc/UTC
RUN ln -snf /usr/share/zoneinfo/$MASCOPE_TIMEZONE /etc/localtime && echo $MASCOPE_TIMEZONE > /etc/timezone

WORKDIR /app

ENV MASCOPE_PATH=/app

# copy build outputs
COPY --from=frontend-build /app/dist /app/frontend/

# the rendered docs site, served by nginx at /docs/
COPY --from=docs-build /docs-src/site /app/docs/

# stage both nginx configs; the entrypoint picks one based on MASCOPE_TLS
COPY server/frontend/nginx.conf /etc/nginx/mascope/nginx.conf
COPY server/frontend/nginx.http.conf /etc/nginx/mascope/nginx.http.conf
COPY server/frontend/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Load the njs module (bundled in the image) and stage the rate-limit key
# script. nginx.conf keys the per-client limit zones on a /64-masked client
# address computed by njs; load_module must be in the main context, which the
# conf.d-included site config cannot reach, so add it to the base nginx.conf.
RUN sed -i '1i load_module /etc/nginx/modules/ngx_http_js_module.so;' /etc/nginx/nginx.conf
COPY server/frontend/ratelimit_key.js /etc/nginx/njs/ratelimit_key.js

# SERVE API & FRONTEND (HTTPS by default; MASCOPE_TLS=off serves HTTP on :80)
CMD ["/docker-entrypoint.sh"]
