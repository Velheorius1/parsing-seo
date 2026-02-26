FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Install system dependencies (cron for scheduling)
RUN apt-get update && \
    apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only — smaller image)
RUN playwright install chromium --with-deps

# Copy crawler code
COPY . .

# Setup cron: every 2 hours
RUN echo "0 */2 * * * cd /app && python -m crawler.main >> /var/log/crawler.log 2>&1" > /etc/cron.d/crawler-cron && \
    chmod 0644 /etc/cron.d/crawler-cron && \
    crontab /etc/cron.d/crawler-cron

# Create log file
RUN mkdir -p /var/log && touch /var/log/crawler.log

# Entrypoint: run once immediately, then start cron daemon
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
