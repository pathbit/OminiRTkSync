#!/bin/sh
set -e

echo "======================================================================"
echo "🧪 Running unit tests in Docker container (zero host dependencies)"
echo "======================================================================"

if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Error: Docker not found. The only requirement is having Docker installed." >&2
    exit 1
fi

docker run --rm -v "$(pwd)":/app -w /app -e PYTHONPATH=/app/src python:3.14-alpine python3 -m unittest discover -s tests -p "test_*.py"

echo "======================================================================"
echo "✅ All tests passed successfully inside the container!"
echo "======================================================================"

