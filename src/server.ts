import express, { Request, Response } from 'express';
import cors from 'cors';

const startTime = Date.now();

export function createServer() {
  const app = express();

  app.use(cors());
  app.use(express.json());

  // Health check endpoints — Express automatically handles both GET and HEAD requests!
  const healthHandler = (req: Request, res: Response) => {
    const uptimeSeconds = Math.floor((Date.now() - startTime) / 1000);
    res.status(200).json({
      status: 'ok',
      message: 'Bot is healthy and running!',
      uptime_seconds: uptimeSeconds,
      service: 'Everything Downloader Bot (TypeScript)',
      timestamp: new Date().toISOString()
    });
  };

  app.get(['/', '/health', '/healthz', '/ping', '/status'], healthHandler);
  app.head(['/', '/health', '/healthz', '/ping', '/status'], healthHandler);

  // 404 handler for invalid routes
  app.use((req: Request, res: Response) => {
    res.status(404).type('text/plain').send('Not Found');
  });

  return app;
}

export function startServer(port: number = Number(process.env.PORT) || 10000) {
  const app = createServer();
  return app.listen(port, '0.0.0.0', () => {
    console.log(`✅ Express Health Check Server listening on http://0.0.0.0:${port}`);
    console.log(`   Health endpoints available at: /health, /healthz, /ping, /status, /`);
    console.log(`   UptimeRobot HEAD & GET requests full support enabled.`);
  });
}
