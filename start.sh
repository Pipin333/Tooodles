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
docker run -d --name tooodles --restart always --env-file .env tooodles-bot

echo "📋 Mostrando logs en tiempo real (Ctrl+C para salir)..."
docker logs -f tooodles
