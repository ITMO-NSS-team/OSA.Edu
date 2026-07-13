FROM node:22-bookworm-slim

WORKDIR /app

ENV NODE_ENV=development \
    HOST=0.0.0.0 \
    PORT=8787 \
    WEB_ORIGIN=http://127.0.0.1:5173,http://localhost:5173 \
    CHOKIDAR_USEPOLLING=true

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173 8787

CMD ["npm", "run", "dev:docker"]
