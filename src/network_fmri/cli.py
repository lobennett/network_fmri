"""Registry-driven command dispatch for network_fmri."""

from __future__ import annotations

import sys

from network_fmri.registry import command_usage, resolve_command


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        sys.stdout.write(command_usage())
        return 0

    resolved = resolve_command(args)
    if resolved is None:
        sys.stderr.write(command_usage())
        return 2

    command, remaining = resolved
    return command.load()(remaining)


if __name__ == "__main__":
    sys.exit(main())
