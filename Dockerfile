FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Instala FFmpeg y paquetes de sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    (ln -sf /usr/bin/nodejs /usr/bin/node 2>/dev/null || true) && \
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
