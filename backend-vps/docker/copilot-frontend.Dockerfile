FROM node:20-alpine
WORKDIR /app

COPY copilot-frontend/package.json copilot-frontend/package-lock.json* ./ 
RUN npm install --legacy-peer-deps
COPY copilot-frontend ./ 
RUN npm run build

ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["npm", "run", "start"]
