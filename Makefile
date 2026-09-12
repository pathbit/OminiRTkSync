.PHONY: test test-container venv run status docker-build docker-run clean

VENV ?= .venv
PYTHON ?= $(shell which $(VENV)/bin/python3 2>/dev/null || which python3 2>/dev/null)

# Executa testes: se o virtualenv existir e tiver python, usa o venv local; caso contrario, roda em container Docker
test:
	@if [ -x "$(VENV)/bin/python3" ]; then \
		echo "Executando testes no ambiente virtual local ($(VENV))..."; \
		PYTHONPATH=src $(VENV)/bin/python3 -m unittest discover -s tests -p "test_*.py"; \
	elif command -v python3 >/dev/null 2>&1; then \
		echo "Executando testes com python3 do host..."; \
		PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"; \
	else \
		echo "Python local nao detectado. Executando testes diretamente em container Docker..."; \
		$(MAKE) test-container; \
	fi

# Executa testes exclusivamente em container Docker (zero dependencias no host alem do Docker)
test-container:
	docker run --rm -v "$$(pwd)":/app -w /app -e PYTHONPATH=/app/src python:3.14-alpine python3 -m unittest discover -s tests -p "test_*.py"

venv:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .

run:
	PYTHONPATH=src $(PYTHON) -m omini_rtksync.cli --daemon

status:
	PYTHONPATH=src $(PYTHON) -m omini_rtksync.cli --status

docker-build:
	docker build -t ominirtksync:latest -t ghcr.io/pathbit/ominirtksync:latest .

docker-run:
	docker run --rm -it --name router-sync -p 9191:9191 ominirtksync:latest

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
