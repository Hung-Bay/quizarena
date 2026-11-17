# ---------------------------------------------------------------------------
# Stage 1: build — biên dịch mã nguồn React TypeScript Vite
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime — phục vụ file tĩnh bằng Nginx Alpine siêu nhẹ (NF-9)
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1/healthz"]

CMD ["nginx", "-g", "daemon off;"]
