"""Run the local publisher without editing application source or saving tokens."""
from pathlib import Path
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from workspace_publisher.__main__ import main as cli_main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        return cli_main()
    from workspace_publisher.gui import main as gui_main
    return gui_main(Path(__file__).resolve().parent)


if __name__ == "__main__":
    raise SystemExit(main())
