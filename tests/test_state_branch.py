"""Exercise the Git state branch locally, without touching GitHub."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/persist_state.py"


class StateBranchTests(unittest.TestCase):
    def test_state_commits_are_separate_and_append_to_previous_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bare, code = root / "remote.git", root / "code"
            env = dict(os.environ)
            def command(*args, cwd=code):
                return subprocess.check_output(args, cwd=cwd, env=env, text=True, stderr=subprocess.STDOUT).strip()
            command("git", "init", "--bare", str(bare), cwd=root)
            command("git", "init", "--initial-branch=main", str(code), cwd=root)
            command("git", "config", "user.name", "Test")
            command("git", "config", "user.email", "test@example.invalid")
            command("git", "remote", "add", "origin", str(bare))
            (code / "code.py").write_text("# source code\n")
            command("git", "add", "code.py")
            command("git", "commit", "-m", "Initial code")
            head = command("git", "rev-parse", "HEAD")
            (code / ".state").mkdir()
            for name in ("state.json", "run-status.json"):
                (code / ".state" / name).write_text('{}\n')
            command(sys.executable, str(SCRIPT))
            first = command("git", "--git-dir=" + str(bare), "rev-parse", "news-state")
            paths = command("git", "--git-dir=" + str(bare), "ls-tree", "--name-only", "news-state")
            self.assertEqual(set(paths.splitlines()), {"state.json", "run-status.json"})
            self.assertEqual(command("git", "rev-parse", "HEAD"), head)
            command("git", "fetch", "origin", "news-state:refs/remotes/origin/news-state")
            (code / ".state/state.json").write_text('{"second":true}\n')
            command(sys.executable, str(SCRIPT))
            parent = command("git", "--git-dir=" + str(bare), "rev-parse", "news-state^")
            self.assertEqual(parent, first)
            self.assertEqual(command("git", "rev-parse", "HEAD"), head)
