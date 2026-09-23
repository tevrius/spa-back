"""Export the deterministic OpenAPI contract without connecting to PostgreSQL."""

import argparse
import json
from pathlib import Path

from app.main import app


def render_openapi() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/openapi.json"))
    parser.add_argument(
        "--check", action="store_true", help="Fail if the saved contract is outdated"
    )
    args = parser.parse_args()
    content = render_openapi()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            parser.exit(1, "OpenAPI snapshot is missing or outdated; run the exporter.\n")
        print("OpenAPI snapshot is up to date")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(f"Exported OpenAPI to {args.output}")


if __name__ == "__main__":
    main()
