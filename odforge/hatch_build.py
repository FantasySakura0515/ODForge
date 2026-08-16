"""Build the web cockpit into the wheel — reproducibly, or not at all.

Before this hook, ``odforge serve`` located the frontend by walking *up from the
source file* to ``odforge/web/dist``. That path exists only in a git checkout, so
every wheel installed anywhere else silently ran API-only: ``GET /`` returned 404
and the failure was indistinguishable from "you forgot to build it". Worse, the
build was whatever happened to be sitting in the developer's ``web/dist`` at the
time — an untracked directory, from an unknown commit, installed by no recorded
command.

This hook fixes both halves:

* **Reproducible.** The frontend is built here, by this hook, with ``npm ci``
  against the committed ``package-lock.json`` — not ``npm install``, which is
  free to resolve newer versions, and not "whatever is already in dist".
* **Fail at build time, not at run time.** If the assets are not in the wheel,
  the build *fails*. An API-only wheel is now something you ask for explicitly
  (``ODFORGE_API_ONLY=1``), not something you discover in production.

``ODFORGE_SKIP_WEB_BUILD=1`` reuses an existing ``web/dist`` (for CI jobs that
already ran the frontend build and want to package that exact artifact) — it
skips the *build*, never the verification.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Where the built assets live inside the installed package. ``importlib.resources``
# finds this at run time; see ``webapi.frontend_dist``.
PACKAGE_WEBUI = Path("src") / "odforge" / "webui"
WEB_SOURCE = Path("web")


class WebUIBuildHook(BuildHookInterface):
    PLUGIN_NAME = "odforge-webui"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        target = root / PACKAGE_WEBUI
        web = root / WEB_SOURCE

        if version == "editable":
            # `pip install -e .` is a development install: the source tree's own
            # `web/dist` is already found by frontend_dist()'s second lookup, and
            # requiring Node for every editable install would make the Python
            # test job depend on a toolchain it has no other use for.
            return

        if os.environ.get("ODFORGE_API_ONLY") == "1":
            self.app.display_waiting(
                "ODFORGE_API_ONLY=1 — building a deliberately API-only wheel "
                "(no web cockpit; `GET /` will 404)."
            )
            return

        if os.environ.get("ODFORGE_SKIP_WEB_BUILD") != "1":
            self._build_frontend(web)

        dist = web / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError(
                f"web frontend not built: {dist / 'index.html'} is missing.\n"
                "Run `npm ci && npm run build` in odforge/web, or set "
                "ODFORGE_API_ONLY=1 to intentionally build an API-only wheel."
            )

        # Staged inside the package directory, so hatchling's normal selector
        # picks it up as package data. ``artifacts`` in pyproject keeps it
        # included even though it is a build product (and gitignored).
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(dist, target)

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        """Remove the staged copy so a source tree is not left dirty."""
        shutil.rmtree(Path(self.root) / PACKAGE_WEBUI, ignore_errors=True)

    def _build_frontend(self, web: Path) -> None:
        if not (web / "package.json").is_file():
            raise RuntimeError(f"no frontend source at {web}")
        lockfile = web / "package-lock.json"
        if not lockfile.is_file():
            raise RuntimeError(
                f"{lockfile} is missing — a reproducible build needs the "
                "lockfile. Commit it, or set ODFORGE_SKIP_WEB_BUILD=1 to "
                "package an already-built web/dist."
            )
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if npm is None:
            raise RuntimeError(
                "npm not found on PATH, so the web cockpit cannot be built. "
                "Install Node.js, or set ODFORGE_SKIP_WEB_BUILD=1 to package an "
                "existing web/dist, or ODFORGE_API_ONLY=1 for an API-only wheel."
            )
        for argv in (["ci"], ["run", "build"]):
            self.app.display_waiting(f"web: npm {' '.join(argv)}")
            # Pin the encoding: ``text=True`` alone decodes with the system
            # locale, and npm/vite emit UTF-8 box-drawing characters — on a
            # cp950 console that raises mid-capture and loses the real error.
            proc = subprocess.run(
                [npm, *argv],
                cwd=web,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                sys.stderr.write(proc.stdout or "")
                sys.stderr.write(proc.stderr or "")
                raise RuntimeError(
                    f"`npm {' '.join(argv)}` failed with exit code "
                    f"{proc.returncode}; the wheel would ship without its UI."
                )
