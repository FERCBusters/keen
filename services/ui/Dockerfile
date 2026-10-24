# Build from the KEEN repository root:
# docker build -f services/ui/Dockerfile -t keen-ui .
# MOSP is a public Git-backed npm dependency pinned by commit in package.json/lock.
# Base images are pinned by digest; tools/docker_image_pins.py keeps them current.

FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS vendor
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

# Build the pinned npm package's assets (uses only its included source/scripts).
RUN npm --prefix node_modules/@fercbusters/mosp-design-system run build

# Copy source and build vendored static assets into public/vendor
COPY services/ui/scripts ./scripts
COPY services/ui/public ./public
RUN npm run build

FROM nginx:1.30-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951

COPY services/ui/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=vendor /build/dist/ /usr/share/nginx/html/

# Make the npm manifests available in the runtime image so you can copy them
# out after a build (e.g. docker cp) without needing to run npm on the host.
RUN mkdir -p /opt/ui-build
COPY --from=vendor /build/package.json /opt/ui-build/package.json
COPY --from=vendor /build/package-lock.json /opt/ui-build/package-lock.json

EXPOSE 80
