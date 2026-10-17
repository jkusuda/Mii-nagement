"""A stand-in for the `gh` CLI, so island tests clone without the network.

Tests point github.GH at [python, this file]. It understands:
    gh auth status              exit 1 when FAKE_GH_LOGGED_OUT is set
    gh repo clone owner/name D  `git clone $FAKE_GH_ROOT/owner/name.git D` (a local bare repo
                                stands in for GitHub; its path stays the origin, so pushes work)
"""

import os
import subprocess
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    if os.environ.get("FAKE_GH_LOGGED_OUT"):
        print("You are not logged into any GitHub hosts. To log in, run: gh auth login", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if args[:2] == ["repo", "clone"]:
    source = os.path.join(os.environ["FAKE_GH_ROOT"], args[2] + ".git")
    if not os.path.isdir(source):
        print(f"GraphQL: Could not resolve to a Repository with the name '{args[2]}'.", file=sys.stderr)
        sys.exit(1)
    sys.exit(subprocess.run(["git", "clone", "-q", source, args[3]]).returncode)
print(f"fake gh: unknown command {args}", file=sys.stderr)
sys.exit(2)
