FROM node:20-alpine AS deps
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

FROM node:20-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . ./
RUN mkdir -p public
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=caddy:2.8-alpine /usr/bin/caddy /usr/local/bin/caddy

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
COPY ./internal.Caddyfile /app/Caddyfile.internal
COPY ./scripts/start-with-internal-tls.sh /app/start-with-internal-tls.sh
RUN chmod +x /app/start-with-internal-tls.sh

USER nextjs
EXPOSE 3443
CMD ["/app/start-with-internal-tls.sh"]
