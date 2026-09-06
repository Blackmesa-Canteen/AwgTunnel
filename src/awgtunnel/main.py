"""Application entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from .application import AwgTunnelApplication  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    application = AwgTunnelApplication()
    return application.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
