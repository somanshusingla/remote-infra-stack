ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS build
ARG NPM_VERSION
WORKDIR /app
RUN npm install --global "npm@${NPM_VERSION}" \
    && test "$(npm --version)" = "${NPM_VERSION}"
COPY vendor/chromadb-admin/package.json vendor/chromadb-admin/package-lock.json ./
COPY --chmod=0755 images/chromadb-admin/install-dependencies.sh /usr/local/bin/chroma-admin-install-dependencies
RUN EXPECTED_NPM_VERSION="${NPM_VERSION}" /usr/local/bin/chroma-admin-install-dependencies
COPY vendor/chromadb-admin/ ./
RUN npm run build && npm prune --omit=dev

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3001
CMD ["npm", "start"]
