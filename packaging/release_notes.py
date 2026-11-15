"""Print the GitHub Release notes for a version: how to install on Windows and Ubuntu,
then that version's section of CHANGELOG.md.   Usage: python packaging/release_notes.py 0.3.0
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

INSTALL = """\
## Download and install

**Windows:** download `stitchwright.exe` below and double-click it. Nothing to install.
If Windows SmartScreen warns about it, choose *More info → Run anyway*.

**Ubuntu (22.04 or later):** download `{deb}` below, then double-click it to install, or
run this in a terminal (it always works):

    sudo apt install ./Downloads/{deb}

Stitchwright then appears in the app menu, and double-clicking a `.pes` or `.stitchwright`
file opens it. To remove it: `sudo apt remove stitchwright`.
"""


def changelog_section(text: str, version: str) -> str:
    """The body of '## <version> (...)' in the changelog, or '' if there is none."""
    m = re.search(rf"^## {re.escape(version)}\b.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def notes(version: str, changelog: str) -> str:
    out = INSTALL.format(deb=f"stitchwright_{version}_amd64.deb")
    changes = changelog_section(changelog, version)
    if changes:
        out += f"\n## What's new in {version}\n\n{changes}\n"
    return out + (f"\nFull history: [CHANGELOG.md](https://github.com/MRC757/stitchwright/blob/"
                  f"v{version}/CHANGELOG.md)\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")         # the notes have arrows and quotes
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        sys.stdout.write(notes(sys.argv[1], f.read()))
