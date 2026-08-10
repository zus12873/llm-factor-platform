"""Export the OpenAPI schema so the frontend can generate types from it.

Run offline: the app is built with an in-memory database and the offline model
double, so exporting never needs credentials and never opens a socket. That
matters because type generation belongs in the build, and a build step that
requires production secrets is a build step people skip.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from factor_platform.main import create_app
from factor_platform.settings import Settings


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/src/api/openapi.json")
    app = create_app(settings=Settings(app_env="test"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
