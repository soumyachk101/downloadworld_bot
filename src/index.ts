import dotenv from 'dotenv';
import { startServer } from './server.js';
import { setupBot } from './bot.js';

dotenv.config();

const port = Number(process.env.PORT) || 10000;
const token = process.env.BOT_TOKEN;

// 1. Start Express Health Server (Supports UptimeRobot GET & HEAD requests)
const server = startServer(port);

// 2. Initialize Telegram Bot with retry logic for 409 Conflict / rolling deploy support
if (!token) {
  console.warn('⚠️  BOT_TOKEN environment variable is not defined!');
  console.warn('   Express Health check server will continue running for deployment checks.');
} else {
  console.log('🤖 Starting Telegram Bot via grammY...');
  const bot = setupBot(token);

  const startBotWithRetry = async (retries = 10, delayMs = 5000) => {
    try {
      // Clear old webhooks to prevent conflicts with long polling
      await bot.api.deleteWebhook({ drop_pending_updates: true }).catch(() => {});

      await bot.start({
        drop_pending_updates: true,
        onStart: (botInfo) => {
          console.log(`✅ Bot successfully started as @${botInfo.username}`);
        }
      });
    } catch (err: any) {
      console.error(`⚠️ Bot polling conflict or connection error: ${err.message || err}`);
      if (retries > 0) {
        console.log(`🔄 Retrying bot polling in ${delayMs / 1000}s... (${retries} retries left)`);
        setTimeout(() => startBotWithRetry(retries - 1, delayMs), delayMs);
      } else {
        console.error('❌ Max retries reached for bot long-polling. Health check server remains active.');
      }
    }
  };

  startBotWithRetry();

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
