# --- build React ---
FROM node:18-alpine AS webbuild
WORKDIR /src
COPY web/package*.json web/
RUN cd web && npm ci
COPY web web
RUN cd web && npm run build

# --- runtime ---
FROM node:18-alpine
WORKDIR /usr/src/app
COPY package*.json ./
RUN npm ci --omit=dev
COPY api api
COPY --from=webbuild /src/web/dist web/dist
EXPOSE 8080
CMD ["node","api/server.js"]
