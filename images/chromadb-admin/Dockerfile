ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY vendor/chromadb-admin/package.json vendor/chromadb-admin/package-lock.json ./
RUN npm ci
COPY vendor/chromadb-admin/ ./
RUN npm run build && npm prune --omit=dev

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3001
CMD ["npm", "start"]
