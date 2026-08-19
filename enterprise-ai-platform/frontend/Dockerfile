# Multi-stage Dockerfile for React + Vite frontend
# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

# Cache dependencies
COPY package.json package-lock.json* ./
RUN npm ci --frozen-lockfile

# Build app
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_AZURE_AD_CLIENT_ID
ARG VITE_AZURE_AD_TENANT_ID
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_AZURE_AD_CLIENT_ID=$VITE_AZURE_AD_CLIENT_ID
ENV VITE_AZURE_AD_TENANT_ID=$VITE_AZURE_AD_TENANT_ID

RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:1.25-alpine

# Security hardening
RUN addgroup -g 101 -S nginx \
    && adduser -S -D -H -u 101 -h /var/cache/nginx -s /sbin/nologin -G nginx -g nginx nginx

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:80/health || exit 1

EXPOSE 80

USER nginx

CMD ["nginx", "-g", "daemon off;"]
