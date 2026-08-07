import { Bot, InlineKeyboard, InputFile, GrammyError, HttpError } from 'grammy';
import fs from 'fs';
import path from 'path';
import os from 'os';
import crypto from 'crypto';
import QRCode from 'qrcode';
import { DownloaderService } from './downloader.js';

const downloader = new DownloaderService();
const botStartTime = Date.now();
let totalDownloadsCount = 0;

// Store detected URLs mapped to short hex keys so callback_data never exceeds Telegram's 64-byte limit
interface StoredUrl {
  url: string;
  createdAt: number;
}
const urlStore = new Map<string, StoredUrl>();

function storeUrl(url: string): string {
  const key = crypto.randomBytes(6).toString('hex'); // 12 characters
  urlStore.set(key, { url, createdAt: Date.now() });

  // Maintain cache size
  if (urlStore.size > 1000) {
    const now = Date.now();
    for (const [k, val] of urlStore.entries()) {
      if (now - val.createdAt > 3600000) {
        urlStore.delete(k);
      }
    }
  }
  return key;
}

function getStoredUrl(keyOrUrl: string): string {
  const entry = urlStore.get(keyOrUrl);
  return entry ? entry.url : keyOrUrl;
}

export function setupBot(token: string): Bot {
  const bot = new Bot(token);

  // ─── Global Error Handler ──────────────────────────────────────────────────
  bot.catch((err) => {
    const ctx = err.ctx;
    console.error(`[bot.catch] Error while handling update ${ctx.update.update_id}:`);
    const e = err.error;
    if (e instanceof GrammyError) {
      console.error('[bot.catch] Telegram API error:', e.description);
    } else if (e instanceof HttpError) {
      console.error('[bot.catch] Network error connecting to Telegram:', e);
    } else {
      console.error('[bot.catch] Unhandled error:', e);
    }
  });

  const getMainMenuKeyboard = () => {
    return new InlineKeyboard()
      .text('📖 Features & Guide', 'show_help')
      .text('📊 System Stats', 'show_stats')
      .row()
      .text('⚡ Commands List', 'show_commands')
      .url('📢 Updates Channel', 'https://t.me/telegram');
  };

  const getWelcomeMessageText = (firstName: string) => {
    return `🌟 **WELCOME TO EVERYTHING DOWNLOADER ULTRA** 🌟\n\n` +
      `Hello **${firstName}**! 👋\n` +
      `Your high-speed, all-in-one social media downloader bot.\n\n` +
      `✨ **Supported Platforms:**\n` +
      `🔴 **YouTube** • Shorts, Music & HD Videos\n` +
      `📸 **Instagram** • Reels, Posts, Stories & IGTV\n` +
      `🎵 **TikTok** • No-Watermark HD Clips\n` +
      `𝕏 **Twitter/X** • Media Clips & Videos\n` +
      `📘 **Facebook** • Public Videos & Reels\n` +
      `🤖 **Reddit** • Videos with HD Audio\n` +
      `📌 **Pinterest** • Media Pins & Gifs\n\n` +
      `⚡ **Bot Capabilities:**\n` +
      `• **Auto Dual Download:** Paste link -> Get **MP4 + MP3** together!\n` +
      `• **Smart Status Bar:** Clean header indicator without chat spam\n` +
      `• **Instant Reactions:** 👀 Emoji confirmation\n\n` +
      `💡 *Simply paste any link in chat to start downloading!*`;
  };

  // ─── Command: /start ────────────────────────────────────────────────────────
  bot.command('start', async (ctx) => {
    const firstName = ctx.from?.first_name || 'User';
    const welcomeMsg = getWelcomeMessageText(firstName);
    const keyboard = getMainMenuKeyboard();
    const logoPath = path.join(process.cwd(), 'logo.jpg');

    if (fs.existsSync(logoPath)) {
      try {
        await ctx.replyWithPhoto(new InputFile(logoPath), {
          caption: welcomeMsg,
          parse_mode: 'Markdown',
          reply_markup: keyboard
        });
        return;
      } catch {
        // Fallback to text if photo fails
      }
    }

    await ctx.reply(welcomeMsg, {
      parse_mode: 'Markdown',
      reply_markup: keyboard
    });
  });

  // ─── Command: /help ─────────────────────────────────────────────────────────
  bot.command('help', async (ctx) => {
    const helpMsg = `📖 **EVERYTHING DOWNLOADER USER GUIDE** 📖\n\n` +
      `🎯 **How to Download Media:**\n` +
      `1️⃣ Paste any URL directly into this chat.\n` +
      `2️⃣ The bot will react with 👀 and process the link.\n` +
      `3️⃣ Both high-definition **MP4 Video** and **MP3 Audio** will be delivered!\n\n` +
      `📌 **Manual Commands:**\n` +
      `• \`/mp3 <link>\` — Extract audio only\n` +
      `• \`/mp4 <link>\` — Download video only\n` +
      `• \`/qr <text>\` — Create custom QR code\n` +
      `• \`/stats\` — View real-time system performance\n` +
      `• \`/start\` — Open main interactive menu\n\n` +
      `🚀 *Supported 1000+ sites powered by yt-dlp core!*`;

    await ctx.reply(helpMsg, {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu')
    });
  });

  // ─── Command: /stats ────────────────────────────────────────────────────────
  bot.command('stats', async (ctx) => {
    const uptimeSec = Math.floor((Date.now() - botStartTime) / 1000);
    const hours = Math.floor(uptimeSec / 3600);
    const mins = Math.floor((uptimeSec % 3600) / 60);
    const secs = uptimeSec % 60;
    const memUsage = (process.memoryUsage().heapUsed / 1024 / 1024).toFixed(2);

    const statsMsg = `📊 **EVERYTHING DOWNLOADER LIVE STATS** 📊\n\n` +
      `⏱ **Uptime:** \`${hours}h ${mins}m ${secs}s\`\n` +
      `💾 **RAM Usage:** \`${memUsage} MB\`\n` +
      `📥 **Total Downloads:** \`${totalDownloadsCount}\`\n` +
      `💻 **Node Engine:** \`${process.version}\`\n` +
      `🖥 **OS Platform:** \`${os.type()} (${os.arch()})\`\n` +
      `🟢 **System Health:** \`100% Operational\``;

    await ctx.reply(statsMsg, {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu')
    });
  });

  // ─── Command: /mp3 ──────────────────────────────────────────────────────────
  bot.command('mp3', async (ctx) => {
    const url = ctx.match?.trim();
    if (!url) {
      return ctx.reply('⚠️ **Please provide a URL!**\nUsage: `/mp3 https://youtube.com/...`', { parse_mode: 'Markdown' });
    }

    try {
      await ctx.react('👀');
    } catch {
      if (ctx.message && ctx.chat) {
        await ctx.api.setMessageReaction(ctx.chat.id, ctx.message.message_id, [{ type: 'emoji', emoji: '👀' }]).catch(() => {});
      }
    }

    const actionInterval = setInterval(() => {
      ctx.replyWithChatAction('upload_document').catch(() => {});
    }, 4000);
    ctx.replyWithChatAction('upload_document').catch(() => {});

    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_mp3_'));

    try {
      const result = await downloader.downloadAudio(url, tempDir);
      await ctx.replyWithAudio(new InputFile(result.filePath), {
        title: result.title,
        caption: `🎧 **${result.title}**\n\n🎼 **Format:** High Quality MP3 Audio\n⚡ *Powered by Everything Downloader*`,
        parse_mode: 'Markdown'
      });
      totalDownloadsCount++;
    } catch (err: any) {
      await ctx.reply(`❌ **Audio Download Failed**\n\n📌 **Reason:** \`${err.message}\``, { parse_mode: 'Markdown' });
    } finally {
      clearInterval(actionInterval);
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  // ─── Command: /mp4 ──────────────────────────────────────────────────────────
  bot.command('mp4', async (ctx) => {
    const url = ctx.match?.trim();
    if (!url) {
      return ctx.reply('⚠️ **Please provide a URL!**\nUsage: `/mp4 https://youtube.com/...`', { parse_mode: 'Markdown' });
    }

    try {
      await ctx.react('👀');
    } catch {
      if (ctx.message && ctx.chat) {
        await ctx.api.setMessageReaction(ctx.chat.id, ctx.message.message_id, [{ type: 'emoji', emoji: '👀' }]).catch(() => {});
      }
    }

    const actionInterval = setInterval(() => {
      ctx.replyWithChatAction('upload_video').catch(() => {});
    }, 4000);
    ctx.replyWithChatAction('upload_video').catch(() => {});

    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_mp4_'));

    try {
      const result = await downloader.downloadVideo(url, tempDir);
      await ctx.replyWithVideo(new InputFile(result.filePath), {
        caption: `🎬 **${result.title}**\n\n📊 **Quality:** Full HD MP4 Video\n⚡ *Powered by Everything Downloader*`,
        parse_mode: 'Markdown'
      });
      totalDownloadsCount++;
    } catch (err: any) {
      await ctx.reply(`❌ **Video Download Failed**\n\n📌 **Reason:** \`${err.message}\``, { parse_mode: 'Markdown' });
    } finally {
      clearInterval(actionInterval);
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  // ─── Command: /qr ───────────────────────────────────────────────────────────
  bot.command('qr', async (ctx) => {
    const text = ctx.match?.trim();
    if (!text) {
      return ctx.reply('⚠️ **Usage:** `/qr your text or link here`', { parse_mode: 'Markdown' });
    }

    try {
      const tempPath = path.join(os.tmpdir(), `qr_${Date.now()}.png`);
      await QRCode.toFile(tempPath, text, { width: 400 });
      await ctx.replyWithPhoto(new InputFile(tempPath), {
        caption: `✅ **QR Code Generated Successfully!**\n\n📌 **Content:** \`${text}\``,
        parse_mode: 'Markdown'
      });
      fs.unlinkSync(tempPath);
    } catch (err: any) {
      await ctx.reply(`❌ **QR Generation failed:** ${err.message}`);
    }
  });

  // ─── Message Handler (Automatic Detection & Dual MP4 + MP3 Download) ───────────────
  bot.on('message:text', async (ctx) => {
    const text = ctx.message.text;
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    const matches = text.match(urlRegex);

    if (!matches || matches.length === 0) {
      return ctx.reply("💡 **Need help?** Simply send me any video or music link from YouTube, Instagram, TikTok, etc.!");
    }

    const targetUrl = matches[0];

    // 1. React to user message with 👀 emoji instantly
    try {
      await ctx.react('👀');
    } catch {
      if (ctx.message && ctx.chat) {
        await ctx.api.setMessageReaction(ctx.chat.id, ctx.message.message_id, [{ type: 'emoji', emoji: '👀' }]).catch(() => {});
      }
    }

    // 2. Step-by-Step Chat Header Status Progression
    // Step 1: Downloading media from server ('typing' -> Header shows "typing...")
    let currentAction: 'typing' | 'upload_video' | 'upload_document' = 'typing';
    const actionInterval = setInterval(() => {
      ctx.replyWithChatAction(currentAction).catch(() => {});
    }, 4000);
    ctx.replyWithChatAction('typing').catch(() => {});

    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_auto_'));
    const videoDir = path.join(tempDir, 'video');
    const audioDir = path.join(tempDir, 'audio');
    fs.mkdirSync(videoDir, { recursive: true });
    fs.mkdirSync(audioDir, { recursive: true });

    try {
      // Step 1: Fetch Video & Audio from media provider concurrently in isolated directories
      const [videoRes, audioRes] = await Promise.allSettled([
        downloader.downloadVideo(targetUrl, videoDir),
        downloader.downloadAudio(targetUrl, audioDir)
      ]);

      let sentMedia = false;

      // Step 2: Upload Video if available
      if (videoRes.status === 'fulfilled') {
        const filePath = videoRes.value.filePath;
        if (fs.existsSync(filePath)) {
          const stats = fs.statSync(filePath);
          if (stats.size > 200 * 1024 * 1024) {
            console.warn(`Video file ${filePath} (${(stats.size / 1024 / 1024).toFixed(1)}MB) exceeds 200MB limit.`);
          } else {
            currentAction = 'upload_video';
            ctx.replyWithChatAction('upload_video').catch(() => {});
            await ctx.replyWithVideo(new InputFile(filePath), {
              caption: `🎬 **${videoRes.value.title}**\n\n📊 **Format:** High-Definition Video (MP4)\n⚡ *Powered by Everything Downloader*`,
              parse_mode: 'Markdown'
            });
            sentMedia = true;
            totalDownloadsCount++;
          }
        }
      }

      // Step 3: Upload Audio if available
      if (audioRes.status === 'fulfilled') {
        const filePath = audioRes.value.filePath;
        if (fs.existsSync(filePath)) {
          const stats = fs.statSync(filePath);
          if (stats.size > 200 * 1024 * 1024) {
            console.warn(`Audio file ${filePath} (${(stats.size / 1024 / 1024).toFixed(1)}MB) exceeds 200MB limit.`);
          } else {
            currentAction = 'upload_document';
            ctx.replyWithChatAction('upload_document').catch(() => {});
            await ctx.replyWithAudio(new InputFile(filePath), {
              title: audioRes.value.title,
              caption: `🎧 **${audioRes.value.title}**\n\n🎼 **Format:** High-Quality MP3 Audio\n⚡ *Powered by Everything Downloader*`,
              parse_mode: 'Markdown'
            });
            sentMedia = true;
            totalDownloadsCount++;
          }
        }
      }

      if (!sentMedia) {
        const errObj = (videoRes as PromiseRejectedResult).reason || (audioRes as PromiseRejectedResult).reason;
        const errMsg = errObj?.message || 'Failed to process media download.';
        if (errMsg.includes('max-filesize') || errMsg.includes('exceeds')) {
          throw new Error('File size exceeds the 200MB upload limit.');
        }
        throw new Error(errMsg);
      }
    } catch (err: any) {
      const msg = err.message || String(err);
      if (msg.includes('sendVideo') || msg.includes('sendAudio') || msg.includes('200MB') || msg.includes('50MB') || msg.includes('413')) {
        await ctx.reply(`❌ **Upload Limit Exceeded**\n\n📌 **Reason:** Media file size exceeds the 200MB upload limit.\n\n💡 *Tip: Try downloading a shorter video or clip under 200MB.*`, { parse_mode: 'Markdown' });
      } else {
        await ctx.reply(`❌ **Download Failed**\n\n📌 **Reason:** \`${msg}\` \n\n💡 *Tip: Make sure the URL is public and accessible.*`, { parse_mode: 'Markdown' });
      }
    } finally {
      clearInterval(actionInterval);
      fs.rmSync(tempDir, { recursive: true, force: true });
    }
  });

  // ─── Callback Query Handlers ────────────────────────────────────────────────
  bot.on('callback_query:data', async (ctx) => {
    const data = ctx.callbackQuery.data;

    if (data === 'show_menu') {
      await ctx.answerCallbackQuery();
      const firstName = ctx.from?.first_name || 'User';
      const welcomeMsg = getWelcomeMessageText(firstName);
      const keyboard = getMainMenuKeyboard();
      return ctx.editMessageText(welcomeMsg, { parse_mode: 'Markdown', reply_markup: keyboard }).catch(async () => {
        await ctx.reply(welcomeMsg, { parse_mode: 'Markdown', reply_markup: keyboard });
      });
    }

    if (data === 'show_help') {
      await ctx.answerCallbackQuery();
      const helpMsg = `📖 **EVERYTHING DOWNLOADER USER GUIDE** 📖\n\n` +
        `🎯 **How to Download Media:**\n` +
        `1️⃣ Paste any URL directly into this chat.\n` +
        `2️⃣ The bot will react with 👀 and process the link.\n` +
        `3️⃣ Both high-definition **MP4 Video** and **MP3 Audio** will be delivered!\n\n` +
        `📌 **Manual Commands:**\n` +
        `• \`/mp3 <link>\` — Extract audio only\n` +
        `• \`/mp4 <link>\` — Download video only\n` +
        `• \`/qr <text>\` — Create custom QR code\n` +
        `• \`/stats\` — View real-time system performance\n` +
        `• \`/start\` — Open main interactive menu\n\n` +
        `🚀 *Supported 1000+ sites powered by yt-dlp core!*`;
      return ctx.editMessageText(helpMsg, {
        parse_mode: 'Markdown',
        reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu')
      }).catch(async () => {
        await ctx.reply(helpMsg, { parse_mode: 'Markdown', reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu') });
      });
    }

    if (data === 'show_stats') {
      await ctx.answerCallbackQuery();
      const uptimeSec = Math.floor((Date.now() - botStartTime) / 1000);
      const hours = Math.floor(uptimeSec / 3600);
      const mins = Math.floor((uptimeSec % 3600) / 60);
      const secs = uptimeSec % 60;
      const memUsage = (process.memoryUsage().heapUsed / 1024 / 1024).toFixed(2);

      const statsMsg = `📊 **EVERYTHING DOWNLOADER LIVE STATS** 📊\n\n` +
        `⏱ **Uptime:** \`${hours}h ${mins}m ${secs}s\`\n` +
        `💾 **RAM Usage:** \`${memUsage} MB\`\n` +
        `📥 **Total Downloads:** \`${totalDownloadsCount}\`\n` +
        `💻 **Node Engine:** \`${process.version}\`\n` +
        `🖥 **OS Platform:** \`${os.type()} (${os.arch()})\`\n` +
        `🟢 **System Health:** \`100% Operational\``;

      return ctx.editMessageText(statsMsg, {
        parse_mode: 'Markdown',
        reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu')
      }).catch(async () => {
        await ctx.reply(statsMsg, { parse_mode: 'Markdown', reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu') });
      });
    }

    if (data === 'show_commands') {
      await ctx.answerCallbackQuery();
      const cmdMsg = `⚡ **QUICK COMMANDS REFERENCE** ⚡\n\n` +
        `• \`/start\` — Launch main dashboard\n` +
        `• \`/help\` — Open complete guide\n` +
        `• \`/stats\` — Check live bot health\n` +
        `• \`/mp3 <url>\` — Download audio track\n` +
        `• \`/mp4 <url>\` — Download video clip\n` +
        `• \`/qr <text>\` — Instant QR code generator`;

      return ctx.editMessageText(cmdMsg, {
        parse_mode: 'Markdown',
        reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu')
      }).catch(async () => {
        await ctx.reply(cmdMsg, { parse_mode: 'Markdown', reply_markup: new InlineKeyboard().text('🔙 Main Menu', 'show_menu') });
      });
    }

    if (data.startsWith('dl_video:')) {
      const key = data.replace('dl_video:', '');
      const url = getStoredUrl(key);
      if (!url) {
        return ctx.answerCallbackQuery({ text: '⚠️ Link session expired. Please send the link again.', show_alert: true });
      }
      await ctx.answerCallbackQuery('Starting video download...');
      if (!ctx.chat) return;

      const actionInterval = setInterval(() => {
        ctx.replyWithChatAction('upload_video').catch(() => {});
      }, 4000);
      ctx.replyWithChatAction('upload_video').catch(() => {});

      const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_video_cb_'));

      try {
        const result = await downloader.downloadVideo(url, tempDir);
        await ctx.replyWithVideo(new InputFile(result.filePath), {
          caption: `🎬 **${result.title}**\n\n📊 **Quality:** Full HD MP4 Video\n⚡ *Powered by Everything Downloader*`,
          parse_mode: 'Markdown'
        });
        totalDownloadsCount++;
      } catch (err: any) {
        await ctx.reply(`❌ **Download failed:** \`${err.message}\``, { parse_mode: 'Markdown' });
      } finally {
        clearInterval(actionInterval);
        fs.rmSync(tempDir, { recursive: true, force: true });
      }
    }

    if (data.startsWith('dl_audio:')) {
      const key = data.replace('dl_audio:', '');
      const url = getStoredUrl(key);
      if (!url) {
        return ctx.answerCallbackQuery({ text: '⚠️ Link session expired. Please send the link again.', show_alert: true });
      }
      await ctx.answerCallbackQuery('Starting audio download...');
      if (!ctx.chat) return;

      const actionInterval = setInterval(() => {
        ctx.replyWithChatAction('upload_document').catch(() => {});
      }, 4000);
      ctx.replyWithChatAction('upload_document').catch(() => {});

      const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl_audio_cb_'));

      try {
        const result = await downloader.downloadAudio(url, tempDir);
        await ctx.replyWithAudio(new InputFile(result.filePath), {
          title: result.title,
          caption: `🎧 **${result.title}**\n\n🎼 **Format:** High Quality MP3 Audio\n⚡ *Powered by Everything Downloader*`,
          parse_mode: 'Markdown'
        });
        totalDownloadsCount++;
      } catch (err: any) {
        await ctx.reply(`❌ **Extraction failed:** \`${err.message}\``, { parse_mode: 'Markdown' });
      } finally {
        clearInterval(actionInterval);
        fs.rmSync(tempDir, { recursive: true, force: true });
      }
    }

    if (data.startsWith('dl_info:')) {
      const key = data.replace('dl_info:', '');
      const url = getStoredUrl(key);
      if (!url) {
        return ctx.answerCallbackQuery({ text: '⚠️ Link session expired. Please send the link again.', show_alert: true });
      }
      await ctx.answerCallbackQuery('Fetching info...');
      try {
        const info = await downloader.getInfo(url);
        const durationStr = info.duration ? `${Math.floor(info.duration / 60)}m ${info.duration % 60}s` : 'Unknown';
        const msg = `ℹ️ **MEDIA INFORMATION** ℹ️\n\n` +
          `📌 **Title:** ${info.title}\n` +
          `👤 **Uploader:** ${info.uploader || 'N/A'}\n` +
          `⏱ **Duration:** ${durationStr}\n` +
          `🌐 **Platform:** ${info.extractor || 'Web'}`;
        await ctx.reply(msg, { parse_mode: 'Markdown' });
      } catch (err: any) {
        await ctx.reply(`❌ **Could not fetch media info:** \`${err.message}\``, { parse_mode: 'Markdown' });
      }
    }
  });

  return bot;
}



