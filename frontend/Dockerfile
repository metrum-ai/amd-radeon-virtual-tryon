# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

# Stage 1: Build VTO frontend with Node 22 Alpine
FROM node:22-alpine AS build

WORKDIR /app

RUN npm install -g pnpm@9

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . ./
RUN pnpm run build

# Stage 2: Serve with nginx (~25MB image)
FROM nginx:1.31.1-alpine3.23-slim AS runtime

LABEL description="Retail Virtual Try-On - Frontend"
LABEL version="1.0.0"

COPY --from=build /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/nginx.conf
COPY locations.conf /etc/nginx/locations.conf

# Plain-HTTP nginx.conf baked in as the default; docker-compose bind-mounts
# nginx.tls.conf over this same path instead when TLS is enabled via
# setup.sh, so no image rebuild is needed to switch between them.
RUN mkdir -p /etc/nginx/certs

RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chown -R nginx:nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx && \
    chown -R nginx:nginx /etc/nginx/certs && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid

EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:5173/health || exit 1

USER nginx

CMD ["nginx", "-g", "daemon off;"]
