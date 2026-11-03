# Atalhos de desenvolvimento do ReqScriba
PYTHON ?= python3

.PHONY: install install-asr run test clean

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

install-asr:
	$(PYTHON) -m pip install -r requirements-asr.txt

run:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(PYTHON) -m pytest -q

clean:
	rm -rf .pytest_cache **/__pycache__
