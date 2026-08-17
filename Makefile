.DEFAULT_GOAL := help
PYTHON ?= python3
RUN_DIR ?= runs/latest
CAPTURE ?= capture.jsonl

.PHONY: help run replay score test validate
help:
	@printf '%s\n' 'make run      - run online evaluation' 'make replay    - run strict captured replay' 'make score     - aggregate evaluation results' 'make test      - run the test suite' 'make validate  - validate generated artifacts'

run:
	$(PYTHON) -m support_eval run

replay:
	$(PYTHON) -m support_eval replay --capture $(CAPTURE)

score:
	$(PYTHON) -m support_eval score

test:
	pytest

validate:
	$(PYTHON) validate.py
