FROM node:20-slim

# Install ffmpeg, python3, curl, and yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    python3 \
    python3-pip \
    curl \
    && curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm install

# Copy application source
COPY . .

# Build TypeScript to JavaScript
RUN npm run build

# Expose port for Render / UptimeRobot health check
EXPOSE 10000

# Start Express server & Telegram bot
CMD ["npm", "start"]
