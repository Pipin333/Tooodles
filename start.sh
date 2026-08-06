#!/bin/bash
set -e

echo "🚀 Actualizando repositorio desde Git..."
git pull

echo "📦 Compilando nueva imagen de Docker..."
docker build -t tooodles-bot .

echo "🛑 Solicitando vaciado suave (esperando fin de la canción actual)..."
docker stop -t 600 tooodles 2>/dev/null || true
docker rm tooodles 2>/dev/null || true

echo "▶️ Lanzando nuevo contenedor Tooodles Bot..."
# Crear carpetas y archivos necesarios en el Host para volumenes persistentes de Docker
touch "$HOME/Tooodles/tooodles.db"
chmod 666 "$HOME/Tooodles/tooodles.db"
mkdir -p "$HOME/Tooodles/logs"
mkdir -p "$HOME/Tooodles/data"

docker run -d --name tooodles --restart always --env-file .env \
  -v "$HOME/Tooodles/tooodles.db:/app/tooodles.db" \
  -v "$HOME/Tooodles/cookies.txt:/app/cookies.txt" \
  -v "$HOME/Tooodles/logs:/app/logs" \
  -v "$HOME/Tooodles/data:/app/data" \
  tooodles-bot

echo "📋 Verificando estado del contenedor..."
sleep 3
docker ps --filter "name=tooodles"
echo "📋 Últimos logs de inicio:"
docker logs --tail 20 tooodles
echo "✅ Despliegue completado exitosamente."
