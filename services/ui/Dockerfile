# Build from the KEEN repository root:
# docker build -f services/ui/Dockerfile -t keen-ui .
# MOSP is a public Git-backed npm dependency pinned by commit in package.json/lock.

FROM node:20-alpine AS vendor
ENV NODE_OPTIONS="--no-network-family-autoselection --dns-result-order=ipv4first"

WORKDIR /build


# npm needs git when installing GitHub dependencies such as Wysi.
# This is only installed in the build/vendor stage, not the runtime nginx image.
RUN apk add --no-cache git ca-certificates \
 && git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" \
 && git config --global --add url."https://github.com/".insteadOf "git@github.com:"

# Lockfile includes the full public Git commit for the design system.
COPY services/ui/package.json services/ui/package-lock.json ./
RUN npm ci \
 && node --input-type=module -e "import { transformSync } from 'esbuild'; transformSync('let x = 1');"

# Preserve KEEN's existing dark-theme/statistics helpers until upstream includes them.
COPY services/ui/patches ./patches
RUN git -C node_modules/@fercbusters/mosp-design-system apply --check /build/patches/mosp-design-system.patch \
 && git -C node_modules/@fercbusters/mosp-design-system apply /build/patches/mosp-design-system.patch

# Build the pinned npm package's assets (uses only its included source/scripts).
RUN npm --prefix node_modules/@fercbusters/mosp-design-system run build

# Copy source and build vendored static assets into public/vendor
COPY services/ui/scripts ./scripts
COPY services/ui/public ./public
RUN npm run build

FROM nginx:1.30.4-alpine

COPY services/ui/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=vendor /build/public/ /usr/share/nginx/html/

# Make the npm manifests available in the runtime image so you can copy them
# out after a build (e.g. docker cp) without needing to run npm on the host.
RUN mkdir -p /opt/ui-build
COPY --from=vendor /build/package.json /opt/ui-build/package.json
COPY --from=vendor /build/package-lock.json /opt/ui-build/package-lock.json

EXPOSE 80
