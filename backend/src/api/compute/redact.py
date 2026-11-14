"""Keeps the compute host's file paths out of what the agent and the user see.

Errors from the host (a shell's "No such file", a Python traceback) carry its directories: the
home directory, the user's name in it, the scratch folders. They're replaced with `***`.
"""

import re

HOME = re.compile(r"/home/[A-Za-z0-9_.-]+")
SCRATCH = re.compile(r"/tmp/icarus-[A-Za-z0-9_.-]+")
MASK = "***"


def hide_host_paths(text: str, workdir: str | None = None) -> str:
    """The text with the host's directories replaced by ***: Icarus's own directory first (so
    `/home/me/icarus/bin/tool` reads `***/bin/tool`), then any other home directory and scratch
    folder."""
    if workdir and workdir.strip("/"):
        text = text.replace(workdir, MASK)
    return SCRATCH.sub(MASK, HOME.sub(MASK, text))
