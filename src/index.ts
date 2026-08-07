import dotenv from 'dotenv';
import { startServer } from './server.js';
import { setupBot } from './bot.js';

dotenv.config();

const port = Number(process.env.PORT) || 10000;
const token = process.env.BOT_TOKEN;

// 1. Start Express Health Server (Supports UptimeRobot GET & HEAD requests)
const server = startServer(port);

// 2. Initialize Telegram Bot
if (!token) {
  console.warn('⚠️  BOT_TOKEN environment variable is not defined!');
  console.warn('   Express Health check server will continue running for deployment checks.');
} else {
  console.log('🤖 Starting Telegram Bot via grammY...');
  const bot = setupBot(token);

  bot.start({
    onStart: (botInfo) => {
      console.log(`✅ Bot successfully started as @${botInfo.username}`);
    }
  });

  // Graceful shutdown handling
  process.once('SIGINT', () => {
    bot.stop();
    server.close();
  });
  process.once('SIGTERM', () => {
    bot.stop();
    server.close();
  });
}
