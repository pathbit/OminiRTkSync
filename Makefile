.PHONY: venv test run status docker-build docker-run clean

# Cria o .env a partir do .env.example. Nunca sobrescreve um .env existente:
# ele carrega os seus segredos, e um `make setup` distraido nao pode apaga-los.
# O docker compose le esse .env sozinho, por estar ao lado do compose.
setup:
	@if [ -f .env ]; then \
		echo ".env ja existe — preservado."; \
	else \
		cp .env.example .env; \
		echo ".env criado a partir de .env.example."; \
	fi
	@echo ""
	@echo "Preencha no .env antes de subir a stack:"
	@grep -nE '^[A-Z_]+=$$' .env | sed 's/^/   linha /' || echo "   (nada obrigatorio em branco)"
	@echo ""
	@echo "O painel usa DASHBOARD_USER e DASHBOARD_PASSWORD. Sem senha definida,"
	@echo "o primeiro acesso usa a credencial de recuperacao gerada no boot."


VENV ?= .venv
PYTHON ?= $(shell which $(VENV)/bin/python3 2>/dev/null || which python3)

venv:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -p "test_*.py"

run:
	PYTHONPATH=src $(PYTHON) -m omini_rtksync.cli --daemon

status:
	PYTHONPATH=src $(PYTHON) -m omini_rtksync.cli --status

docker-build:
	docker build -t ominirtksync:latest -t ghcr.io/pathbit/ominirtksync:latest .

# O bind em 127.0.0.1 nao e detalhe: este painel le o banco do gateway e mostra
# a saude das credenciais. Sem o prefixo, "-p PORTA:9090" publica em TODA
# interface -- o Wi-Fi do cafe, a VLAN do escritorio -- enquanto os composes
# deste repo publicam so no loopback. Comentario FORA da receita: linha iniciada
# por # dentro de um alvo vai para o shell e aparece na saida.
docker-run:
	docker run --rm -it --name ominirtk-sync -p 127.0.0.1:9092:9090 ominirtksync:latest

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
