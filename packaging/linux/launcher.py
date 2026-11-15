"""Entry point of the Linux build (/opt/stitchwright/stitchwright).

One program, two commands (both links to it in /usr/bin):
  stitchwright-gui [FILE]       the window (what the menu entry and double-click run)
  stitchwright COMMAND ...      the command line (list, palette, convert, gui, --help);
                                with no command, or just a file, it opens the window too
"""
import os
import sys

CLI_COMMANDS = {"list", "palette", "convert", "gui"}


def main() -> int:
    name = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if name.endswith("-gui") or not args or (args[0] not in CLI_COMMANDS
                                             and not args[0].startswith("-")):
        from stitchwright.gui import main as gui_main
        return gui_main(args)
    from stitchwright.cli import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
