.PHONY: check test lint actionlint yamllint

check: test lint

test:
	python3 -B -m unittest discover -s tests -v

lint: actionlint yamllint

actionlint:
	actionlint

yamllint:
	yamllint ansible.yaml
