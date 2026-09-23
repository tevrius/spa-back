import subprocess

from app.config import get_settings
from scripts.seed import seed


def main():
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    if get_settings().seed_demo:
        seed()


if __name__ == "__main__":
    main()
