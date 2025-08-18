#Build the web app
FROM node:18-alpine AS webbuild
WORKDIR /src
COPY web/package*.json web/
RUN cd web && npm ci
COPY web web
RUN cd web && npm run build     # -> creates web/dist

# Runtime
FROM node:18-alpine
WORKDIR /usr/src/app
COPY package*.json ./
RUN npm ci --omit=dev

# copy API code
COPY api api

# copy compiled web assets into the image
COPY --from=webbuild /src/web/dist web/dist

EXPOSE 8080
CMD ["node", "api/server.js"]
CMD ["node","api/server.js"]
