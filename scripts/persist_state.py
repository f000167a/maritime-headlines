"""Commit runtime state to news-state, without switching the code checkout."""
import os
from pathlib import Path
import subprocess
import tempfile


def git(*args, **kwargs):
    return subprocess.check_output(["git", *args], text=True, **kwargs).strip()


def main():
    with tempfile.TemporaryDirectory() as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git("read-tree", "--empty", env=env)
        for name in ("state.json", "run-status.json"):
            blob = git("hash-object", "-w", str(Path(".state") / name))
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}", env=env)
        tree = git("write-tree", env=env)
        parent = subprocess.run(["git", "rev-parse", "--verify", "refs/remotes/origin/news-state"],
                                text=True, capture_output=True)
        parents = ["-p", parent.stdout.strip()] if parent.returncode == 0 else []
        commit = git("commit-tree", tree, *parents, input="Update news state after successful deployment\n")
        subprocess.run(["git", "push", "origin", f"{commit}:refs/heads/news-state"], check=True)


if __name__ == "__main__":
    main()
