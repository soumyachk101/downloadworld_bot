FROM node:18-alpine

# Install all required packages: curl, ffmpeg, and yt-dlp from Alpine repos
# yt-dlp from Alpine repos works with musl libc (Alpine's libc) unlike standalone binaries
RUN apk add --no-cache curl ffmpeg yt-dlp

WORKDIR /app

# Install dependencies first (layer caching)
COPY package.json ./
RUN npm install --omit=dev

# Copy application source
COPY . .

CMD ["node", "index.js"]
