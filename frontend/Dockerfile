# The build directory must NOT be /app. Next.js resolves route layouts by
# path, and with the project root at /app the Next app directory is /app/app
# and the /app route is /app/app/app: layouts and pages were mis-paired, so
# "/auth/login" was rendered inside the authenticated AppShell and "/" redirected
# to /app/dashboard, producing a redirect loop in the browser. The same build
# is correct from any other directory (and on Windows).
FROM node:24-alpine AS deps
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /build
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL} \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY}
COPY --from=deps /build/node_modules ./node_modules
COPY frontend ./
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S app && adduser -S app -G app

COPY --from=builder /build/public ./public
COPY --from=builder /build/.next/standalone ./
COPY --from=builder /build/.next/static ./.next/static

USER app

EXPOSE 3000

CMD ["node", "server.js"]
