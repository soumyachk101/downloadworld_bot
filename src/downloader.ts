import { execFile, spawn } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs';
import os from 'os';

const execFileAsync = promisify(execFile);

export interface MediaInfo {
  title: string;
  duration?: number;
  thumbnail?: string;
  uploader?: string;
  extractor?: string;
  formats?: Array<{
    format_id: string;
    ext: string;
    resolution?: string;
    filesize?: number;
    vcodec?: string;
    acodec?: string;
  }>;
}

export class DownloaderService {
  private getCookieArgs(url?: string): string[] {
    const args: string[] = [];
    const ytCookie = process.env.YOUTUBE_COOKIES_FILE || 'youtube_cookies.txt';
    const igCookie = process.env.INSTAGRAM_COOKIES_FILE || 'instagram_cookies.txt';
    const cookiesJsonPath = path.join(process.cwd(), 'cookies.json');

    const isInstagram = url ? /instagram\.com/i.test(url) : false;
    const isYouTube = url ? /(youtube\.com|youtu\.be)/i.test(url) : false;

    if (isInstagram) {
      if (fs.existsSync(igCookie)) {
        args.push('--cookies', igCookie);
      } else if (fs.existsSync(cookiesJsonPath)) {
        try {
          const raw = fs.readFileSync(cookiesJsonPath, 'utf-8');
          const parsed = JSON.parse(raw);
          if (parsed.instagram && Array.isArray(parsed.instagram) && parsed.instagram[0]) {
            args.push('--add-header', `Cookie: ${parsed.instagram[0]}`);
          }
        } catch (e) {
          // ignore parse error
        }
      }
    } else if (isYouTube) {
      if (fs.existsSync(ytCookie)) {
        args.push('--cookies', ytCookie);
      }
    } else {
      if (fs.existsSync(ytCookie)) {
        args.push('--cookies', ytCookie);
      }
      if (fs.existsSync(igCookie)) {
        args.push('--cookies', igCookie);
      }
    }
    return args;
  }

  async getInfo(url: string): Promise<MediaInfo> {
    const cookieArgs = this.getCookieArgs(url);
    const args = [
      '--dump-json',
      '--no-playlist',
      '--skip-download',
      ...cookieArgs,
      url
    ];

    try {
      const { stdout } = await execFileAsync('yt-dlp', args, { timeout: 30000 });
      const data = JSON.parse(stdout);
      return {
        title: data.title || 'Downloaded Media',
        duration: data.duration,
        thumbnail: data.thumbnail,
        uploader: data.uploader || data.channel,
        extractor: data.extractor,
        formats: data.formats || []
      };
    } catch (error: any) {
      throw new Error(`Failed to fetch media metadata: ${error.message || error}`);
    }
  }

  async downloadVideo(url: string, outputDir: string, formatId?: string): Promise<{ filePath: string; title: string }> {
    const cookieArgs = this.getCookieArgs(url);
    const outputTemplate = path.join(outputDir, '%(title).50s.%(ext)s');

    let formatArg = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best';
    if (formatId) {
      formatArg = `${formatId}+bestaudio/best`;
    }

    const args = [
      '-f', formatArg,
      '--merge-output-format', 'mp4',
      '-o', outputTemplate,
      '--no-playlist',
      '--max-filesize', '50M',
      ...cookieArgs,
      url
    ];

    try {
      await execFileAsync('yt-dlp', args, { timeout: 300000 });
      const files = fs.readdirSync(outputDir);
      const downloadedFile = files.find(f => !f.startsWith('.') && (f.endsWith('.mp4') || f.endsWith('.mkv') || f.endsWith('.webm') || f.endsWith('.mov') || f.endsWith('.avi')))
        || files.find(f => !f.startsWith('.') && !f.endsWith('.part') && !f.endsWith('.ytdl') && !f.endsWith('.json') && !f.endsWith('.m4a') && !f.endsWith('.mp3'));
      if (!downloadedFile) {
        throw new Error('Downloaded video file not found in output directory.');
      }
      return {
        filePath: path.join(outputDir, downloadedFile),
        title: downloadedFile
      };
    } catch (error: any) {
      throw new Error(`Download failed: ${error.message || error}`);
    }
  }

  async downloadAudio(url: string, outputDir: string): Promise<{ filePath: string; title: string }> {
    const cookieArgs = this.getCookieArgs(url);
    const outputTemplate = path.join(outputDir, '%(title).50s.%(ext)s');

    const args = [
      '-x',
      '--audio-format', 'mp3',
      '--audio-quality', '0',
      '-o', outputTemplate,
      '--no-playlist',
      '--max-filesize', '50M',
      ...cookieArgs,
      url
    ];

    try {
      await execFileAsync('yt-dlp', args, { timeout: 300000 });
      const files = fs.readdirSync(outputDir);
      const downloadedFile = files.find(f => !f.startsWith('.') && f.endsWith('.mp3'));
      if (!downloadedFile) {
        throw new Error('Downloaded MP3 file not found.');
      }
      return {
        filePath: path.join(outputDir, downloadedFile),
        title: downloadedFile
      };
    } catch (error: any) {
      throw new Error(`Audio download failed: ${error.message || error}`);
    }
  }

  async processFFmpeg(inputPath: string, outputPath: string, extraArgs: string[]): Promise<string> {
    const args = ['-y', '-i', inputPath, ...extraArgs, outputPath];
    try {
      await execFileAsync('ffmpeg', args, { timeout: 120000 });
      return outputPath;
    } catch (error: any) {
      throw new Error(`FFmpeg operation failed: ${error.message || error}`);
    }
  }
}
