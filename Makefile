.PHONY: test package binary clean

test:
	uv run pytest && uv run ruff check .

# Wheel + sdist in dist/. Install anywhere with: uv tool install dist/*.whl
package: test
	uv build

# Single-file executable at dist/unify. Bundles transform.py as it is right
# now, so rebuild after any code change. Runs on the OS/arch it was built on.
binary: test
	uv run --with pyinstaller pyinstaller --onefile --name unify --paths src \
		--collect-submodules unify --distpath dist --workpath build --specpath build \
		--noconfirm --log-level WARN src/unify/__main__.py

clean:
	rm -rf dist build out
