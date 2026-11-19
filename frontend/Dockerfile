FROM node:20.18-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run typecheck && npm test && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
USER root
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
RUN chmod 755 /etc/nginx/snippets \
    && chmod 644 /etc/nginx/snippets/security-headers.conf /etc/nginx/conf.d/default.conf \
    && chmod -R a+rX /usr/share/nginx/html
USER 101
EXPOSE 8080
