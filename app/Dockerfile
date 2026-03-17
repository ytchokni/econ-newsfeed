FROM node:20-slim

WORKDIR /app

RUN groupadd --system appgroup && useradd --system --gid appgroup appuser

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 3000

CMD ["npm", "start"]
