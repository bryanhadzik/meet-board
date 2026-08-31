# syntax=docker/dockerfile:1
FROM node:22-alpine

ENV NODE_ENV=production
WORKDIR /app

# Dependencies first so edits to src/ don't bust the layer cache.
# --omit=optional skips serialport: it needs a native build, and the container
# reads the console over TCP anyway (see README - Docker on Windows cannot pass
# a COM port into a Linux container).
COPY package.json ./
RUN npm install --omit=dev --omit=optional --no-audit --no-fund

COPY src/ ./src/
COPY public/ ./public/

# meet.json is bind-mounted at /data so a new session export needs no rebuild.
ENV BOARD_PORT=8080 \
    BOARD_PUBLIC=/app/public \
    BOARD_MEET=/data/meet.json

# The server writes meet.json here on upload, so it must be writable by the
# unprivileged user below. On a Linux host with a bind mount the HOST ownership
# wins, so there you also need:  sudo chown -R 1000:1000 data
RUN mkdir -p /data && chown node:node /data

EXPOSE 8080
VOLUME ["/data"]

# Compose restarts an unhealthy container; this is what makes it self-healing.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:'+(process.env.BOARD_PORT||8080)+'/api/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

# Run unprivileged. No serial device to open, so no group membership needed.
USER node

CMD ["node", "src/board-server.js"]
