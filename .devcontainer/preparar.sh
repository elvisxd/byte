#!/usr/bin/env bash
# Deja el codespace listo para correr los evals contra un modelo grande.
#
# No descarga ningún modelo: pesan 5-20 GB y cuál bajar depende de qué se quiera
# medir. El README dice cuál y cómo.
set -euo pipefail

echo "→ dependencias de Python"
pip install --quiet uv
uv sync --extra observabilidad

echo "→ Ollama"
curl -fsSL https://ollama.com/install.sh | sh

echo "→ configuración"
if [ ! -f .env ]; then
  cp .env.example .env
  # Claves de juguete: este entorno es efímero y no se expone a internet.
  {
    echo "BYTE_API_KEY=$(openssl rand -hex 32)"
    echo "BYTE_SECRET_KEY=$(openssl rand -hex 32)"
    echo "BYTE_PROJECT_ROOT=$PWD"
  } >> .env
fi

cat <<'FIN'

Listo. Para medir un modelo grande:

  ollama serve &                       # arranca Ollama
  ollama pull qwen3.6:27b              # o el que quieras medir (~17 GB)
  uv run uvicorn api.main:app --port 8000 &
  uv run python -m evals.correr --url http://localhost:8000 --api-key "$BYTE_API_KEY"

Ojo: sin GPU el modelo va a 5-15 tokens/s. Sirve para saber SI resuelve las
tareas, no para cronometrarlo.

FIN
