const fs = require('fs');
const path = require('path');

// Load .env file
const envPath = path.resolve(__dirname, '.env');
const envVars = {};
if (fs.existsSync(envPath)) {
  fs.readFileSync(envPath, 'utf8').split('\n').forEach(line => {
    const match = line.match(/^([^#=]+)=(.*)$/);
    if (match) envVars[match[1].trim()] = match[2].trim();
  });
}

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
      ...envVars
    },
    error_file: './logs/err.log',
    out_file: './logs/out.log',
    log_file: './logs/combined.log',
    time: true
  }]
};
