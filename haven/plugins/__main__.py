"""`python -m haven.plugins <cmd>`: the plugin marketplace's operator surface.

Plain human output on stdout; expected runtime failures on stderr with
exit 1, argparse usage errors with exit 2, success with exit 0. Every
subcommand takes `--data-dir` to override where enablement state is stored
and `--catalog-url` to override the Hub catalog endpoint.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .catalog_client import CatalogFetchError, DEFAULT_CATALOG_URL
from .contracts import InvalidPluginIdError
from .manager import PluginManager, UnknownPluginError
from .registry import PluginRegistry, PluginRegistryError

_DEFAULT_REGISTRY_FILENAME = "plugins.json"


def _manager(args: argparse.Namespace) -> PluginManager:
    data_dir = Path(args.data_dir) if args.data_dir else Path.cwd()
    registry = PluginRegistry(data_dir / _DEFAULT_REGISTRY_FILENAME)
    return PluginManager(registry, catalog_url=args.catalog_url)


def _cmd_list(args: argparse.Namespace) -> int:
    manager = _manager(args)
    view = manager.refresh_catalog()
    if view.catalog_error is not None:
        print(f"catalog: unavailable ({view.catalog_error})", file=sys.stderr)
    if not view.entries:
        print("no plugins in the catalog")
        return 0
    for entry in view.entries:
        d = entry.descriptor
        state = "enabled" if entry.enabled else "disabled"
        print(f"{d.plugin_id} | {d.display_name} | {d.capability.value} | {d.status.value} | {state}")
    return 0


def _cmd_enable(args: argparse.Namespace) -> int:
    manager = _manager(args)
    manager.refresh_catalog()
    manager.enable(args.plugin_id)
    print(f"enabled {args.plugin_id}")
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    manager = _manager(args)
    manager.disable(args.plugin_id)
    print(f"disabled {args.plugin_id}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haven.plugins", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, *arguments: tuple) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name)
        cmd.set_defaults(handler=handler)
        for flags, kwargs in arguments:
            cmd.add_argument(*flags, **kwargs)
        cmd.add_argument("--data-dir", default=None, help="override where enablement state is stored")
        cmd.add_argument("--catalog-url", default=DEFAULT_CATALOG_URL, help="override the Hub catalog endpoint")
        return cmd

    add("list", _cmd_list)
    add("enable", _cmd_enable, (("plugin_id",), {}))
    add("disable", _cmd_disable, (("plugin_id",), {}))
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (CatalogFetchError, UnknownPluginError, InvalidPluginIdError, PluginRegistryError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
