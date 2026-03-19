FROM node:20-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY . ./
ARG VITE_BASE=/expert-console/
ARG VITE_SCENARIO_API_URL=/scenario-api
ENV VITE_BASE=${VITE_BASE}
ENV VITE_SCENARIO_API_URL=${VITE_SCENARIO_API_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY scenario-frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
