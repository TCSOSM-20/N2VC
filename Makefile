clean:
	find . -name __pycache__ -type d -exec rm -r {} +
	find . -name *.pyc -delete
	rm -rf .tox
	rm -rf tests/charms/builds/*
.tox:
	tox -r --notest
test: lint
	tox
lint:
	tox -e lint
package:
	python3 setup.py --command-packages=stdeb.command bdist_deb
