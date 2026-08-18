# syntax=docker/dockerfile:1.7
#
# Development image for both web applications. Production builds are static
# output served by the CDN and are a later-phase concern (SRS §35.7).

FROM node:20-bookworm-slim AS development

ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

WORKDIR /workspace

# Manifests first so a source edit does not reinstall the dependency tree.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY packages/config/package.json ./packages/config/
COPY packages/contracts/package.json ./packages/contracts/
COPY packages/ui/package.json ./packages/ui/
COPY apps/web-tourist/package.json ./apps/web-tourist/
COPY apps/web-console/package.json ./apps/web-console/

RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

COPY . .
