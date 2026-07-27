FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Instala FFmpeg, Curl y Node.js 22.x (Requerido por yt-dlp EJS solver >= 22.0.0)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia e instala dependencias primero para aprovechar el caché de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps || true

# Copia el resto del código del proyecto
COPY . .

# Ejecuta el bot
CMD ["python", "main.py"]
