FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir "discord.py==2.6.4" "aiohttp==3.12.15"
COPY bot.py .
USER 1000:999
CMD ["python", "-u", "bot.py"]
