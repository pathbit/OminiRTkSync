#!/bin/sh
set -e

echo "======================================================================"
echo "🧪 Executando testes unitarios em container Docker (zero dependencias)"
echo "======================================================================"

if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Erro: Docker nao encontrado. O unico pre-requisito e ter o Docker instalado." >&2
    exit 1
fi

docker run --rm -v "$(pwd)":/app -w /app -e PYTHONPATH=/app/src python:3.14-alpine python3 -m unittest discover -s tests -p "test_*.py"

echo "======================================================================"
echo "✅ Todos os testes foram executados e aprovados com sucesso no container!"
echo "======================================================================"
