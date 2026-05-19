module.exports = {
  apps: [{
    name: 'news-events-scraper',
    script: 'main.py',
    interpreter: 'python3',
    cwd: '/home/ubuntu/News_Events_Scraper',
    instances: 1,
    autorestart: true,
    watch: false,
    max_memory_restart: '1G',
    env: {
      NODE_ENV: 'production',
      OPENAI_API_KEY: process.env.OPENAI_API_KEY || ''
    },
    error_file: './logs/err.log',
    out_file: './logs/out.log',
    log_file: './logs/combined.log',
    time: true
  }]
};
