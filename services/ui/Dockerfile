# Keen UI Dockerfile
#
# IMPORTANT: This Dockerfile expects the *build context* to be a directory that
# contains BOTH:
#   - keen/
#   - mosp-design-system/
# (i.e. siblings).
#
# Example:
#   /workspace/
#     keen/
#     mosp-design-system/
#
# Build with:
#   docker build -f keen/services/ui/Dockerfile -t keen-ui /workspace

FROM node:20-alpine AS vendor

WORKDIR /build


# npm needs git when installing GitHub dependencies such as Wysi.
# This is only installed in the build/vendor stage, not the runtime nginx image.
RUN apk add --no-cache git ca-certificates

# Install pinned frontend deps.
#
# If package-lock.json is present, use npm ci for a reproducible install.
# If it's missing, generate it inside this build stage so it can be copied out
# later (see the final stage /opt/ui-build).
COPY keen/services/ui/package*.json ./
RUN if [ -f package-lock.json ]; then \
      npm ci; \
    else \
      npm install; \
    fi \
 && if [ ! -f package-lock.json ]; then npm install --package-lock-only; fi

# Inject MOSP design system (local codebase, no npm registry)
COPY mosp-design-system/ /tmp/mosp-design-system/
RUN (cd /tmp/mosp-design-system && node scripts/build.mjs) \
 && mkdir -p /build/node_modules/@FERCBusters \
 && rm -rf /build/node_modules/@FERCBusters/mosp-design-system \
 && cp -a /tmp/mosp-design-system /build/node_modules/@FERCBusters/mosp-design-system \
 && rm -rf /build/node_modules/@FERCBusters/mosp-design-system/node_modules

# Copy source and build vendored static assets into public/vendor
COPY keen/services/ui/scripts ./scripts
COPY keen/services/ui/public ./public
RUN npm run build

FROM nginx:1.30.4-alpine

COPY keen/services/ui/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=vendor /build/public/ /usr/share/nginx/html/

# Make the npm manifests available in the runtime image so you can copy them
# out after a build (e.g. docker cp) without needing to run npm on the host.
RUN mkdir -p /opt/ui-build
COPY --from=vendor /build/package.json /opt/ui-build/package.json
COPY --from=vendor /build/package-lock.json /opt/ui-build/package-lock.json

EXPOSE 80
