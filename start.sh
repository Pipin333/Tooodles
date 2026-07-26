#!/bin/bash
set -e

echo "🚀 Actualizando repositorio desde Git..."
git pull

echo "🛑 Deteniendo y removiendo contenedor anterior..."
docker stop tooodles 2>/dev/null || true
docker rm tooodles 2>/dev/null || true

echo "📦 Compilando nueva imagen de Docker..."
docker build -t tooodles-bot .

echo "▶️ Lanzando contenedor Tooodles Bot..."
docker run -d --name tooodles --restart always --env-file .env tooodles-bot

echo "📋 Mostrando logs en tiempo real (Ctrl+C para salir)..."
docker logs -f tooodles
