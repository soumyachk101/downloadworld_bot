FROM node:18-alpine

# Install curl and ffmpeg via Alpine package manager
RUN apk add --no-cache curl ffmpeg

WORKDIR /app

# Install dependencies first (layer caching)
COPY package.json ./
RUN npm install --omit=dev

# Copy application source
COPY . .

CMD ["node", "index.js"]
