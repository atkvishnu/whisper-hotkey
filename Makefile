PYTHON ?= python3

.PHONY: help install test check
help:
	@echo "make install  Install Whisper and the hotkey command (see ./install.sh --help)"
	@echo "make test     Run offline automated tests"
	@echo "make check    Run tests, shell syntax checks, and Python compilation"

install:
	./install.sh

test:
	$(PYTHON) -m unittest discover -s tests -v

check: test
	sh -n install.sh
	sh -n scripts/whisper-toggle.sh
	$(PYTHON) -m compileall -q scripts tests
