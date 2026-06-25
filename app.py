"""Entry point.

  Local dev:  uv run flask --app app run
  Production: gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app

The background harvest thread starts on import/boot (see n2g.create_app), not
per request. MUST run as a single worker: in-memory state lives in this one
process.
"""
from __future__ import annotations

import os

# Set N2G_SKIP_DOTENV=1 to ignore any .env entirely (used by tests so an empty
# GITHUB_TOKEN reliably forces mock regardless of a developer's local .env).
if os.environ.get("N2G_SKIP_DOTENV") != "1":
    try:
        from dotenv import dotenv_values, load_dotenv

        load_dotenv()
        # load_dotenv() does not override variables already in the environment, so
        # a stray empty export (e.g. `GITHUB_TOKEN=`) in the shell would shadow the
        # real value in .env and silently force mock mode. Treat empty-as-unset and
        # fill those keys from .env. Non-empty overrides (e.g. DEV_AUTH_BYPASS=1 on
        # the command line) are left untouched.
        for _key, _val in dotenv_values().items():
            if _val and not os.environ.get(_key, "").strip():
                os.environ[_key] = _val
    except ImportError:  # python-dotenv is optional; env may be set externally
        pass

from n2g import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
