"""Run API + durable worker on an isolated migrated database for browser acceptance."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / ".cache" / "e2e" / str(uuid4())
    folder.mkdir(parents=True)
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{(folder / 'test.db').as_posix()}",
        "CHECKPOINT_PATH": str(folder / "checkpoints.sqlite"),
        "LLM_MODE": "mock",
        "RETRIEVAL_MODE": "lexical",
        "DEMO_PASSWORD": "tracelens-local",
        "COOKIE_SECURE": "false",
        "ALLOWED_ORIGINS": "http://127.0.0.1:8011",
        "PYTHONUTF8": "1",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    subprocess.run(
        [sys.executable, "-m", "data.generator.seed", "--no-background"], cwd=ROOT, env=env, check=True
    )
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "apps.worker.main"], cwd=ROOT, env=env))
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "apps.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8011",
                ],
                cwd=ROOT,
                env=env,
            )
        )
        children[-1].wait()
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__":
    main()
