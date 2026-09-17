"""`python -m haven.models <cmd>`: the model manager's operator surface.

Plain human output (ids, kinds, states, problems, resolved id) on stdout;
expected runtime failures on stderr with exit 1, argparse usage errors with
exit 2, success with exit 0. Every subcommand takes `--root` to override
the models root.
"""

from __future__ import annotations

import argparse
import sys

from .catalog import CatalogError, load_catalog
from .discovery import inspect_folder
from .downloader import HashMismatchError, ModelSourceError
from .manager import ModelManager, ModelManagerError
from .states import ModelState
from .storage import StorageError, default_models_root


def _manager(args: argparse.Namespace) -> ModelManager:
    root = args.root if args.root is not None else default_models_root()
    return ModelManager(root)


def _cmd_search(args: argparse.Namespace) -> int:
    entries = ()
    if args.catalog:
        entries = load_catalog(args.catalog)
    else:
        entries = _manager(args).search()
    if args.query:
        needle = args.query.strip().lower()
        entries = tuple(
            e
            for e in entries
            if needle in e.id.lower() or needle in e.description.lower() or needle in e.kind.value
        )
    for entry in entries:
        license_part = entry.license or "unknown"
        print(f"{entry.id} | {entry.kind.value} | {license_part} | {entry.description}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    inspection = _manager(args).inspect_url(args.url)
    m = inspection.manifest
    print(f"id: {m.id}")
    print(f"kind: {m.kind.value}")
    print(f"version: {m.version}")
    print(f"backend: {inspection.backend}")
    print(f"architecture: {m.architecture}")
    print(f"capabilities: {', '.join(sorted(m.capabilities))}")
    print(f"files: {inspection.file_count}")
    print(f"languages: {', '.join(sorted(inspection.languages)) or '-'}")
    print(f"hardware: {', '.join(sorted(inspection.hardware)) or '-'}")
    print(f"license: {inspection.license or 'unknown'}")
    print(f"hash verification: {'yes' if inspection.hash_verification else 'no'}")
    print(f"remote code: {inspection.remote_code}")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    record = _manager(args).install_from_url(args.url)
    print(f"installed {record.id} ({record.state.value}) [{record.source_type.value}]")
    return 0


def _cmd_install_local(args: argparse.Namespace) -> int:
    record = _manager(args).install_local_folder(args.folder)
    print(f"installed {record.id} ({record.state.value}) [local]")
    return 0


def _cmd_add_endpoint(args: argparse.Namespace) -> int:
    record = _manager(args).register_endpoint(args.url)
    print(f"registered {record.id} ({record.state.value}) [endpoint]")
    return 0


def _cmd_add_root(args: argparse.Namespace) -> int:
    for root in _manager(args).add_root(args.path):
        print(f"root: {root}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    manager = _manager(args)
    results = manager.scan()
    if not results:
        print("no candidates (no roots registered?)")
    for result in results:
        problems = "; ".join(result.problems) if result.problems else "-"
        print(f"{result.path} | {result.state.value} | {problems}")
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    manager = _manager(args)
    candidates = [r for r in manager.scan() if str(r.path) == str(args.folder)]
    result = candidates[0] if candidates else inspect_folder(args.folder)
    record = manager.register_candidate(result)
    print(f"registered {record.id} ({record.state.value})")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    state = ModelState(args.state) if args.state else None
    records = _manager(args).list_models(state)
    for record in records:
        print(f"{record.id} | {record.kind.value} | {record.state.value} | {record.source_type.value}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    requires = frozenset(part for part in args.requires.split(",") if part.strip()) if args.requires else frozenset()
    languages = frozenset(part for part in args.languages.split(",") if part.strip()) if args.languages else frozenset()
    descriptor = _manager(args).resolve(args.kind, requires=requires, languages=languages)
    print(descriptor.id)
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    record = _manager(args).load(args.id)
    print(f"{record.id} {record.state.value} (backend: {record.loaded_backend})")
    return 0


def _cmd_unload(args: argparse.Namespace) -> int:
    record = _manager(args).unload(args.id)
    print(f"{record.id} {record.state.value}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    _manager(args).remove(args.id)
    print(f"removed {args.id}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haven.models", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, *arguments: tuple, root: bool = True) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name)
        cmd.set_defaults(handler=handler)
        for flags, kwargs in arguments:
            cmd.add_argument(*flags, **kwargs)
        if root:
            cmd.add_argument("--root", default=None, help="override the models root")
        return cmd

    add("search", _cmd_search, (("query",), {"nargs": "?"}), (("--catalog",), {"default": None}))
    add("inspect", _cmd_inspect, (("url",), {}))
    add("download", _cmd_download, (("url",), {}))
    add("install-local", _cmd_install_local, (("folder",), {}))
    add("add-endpoint", _cmd_add_endpoint, (("url",), {}))
    add("add-root", _cmd_add_root, (("path",), {}))
    add("scan", _cmd_scan)
    add("register", _cmd_register, (("folder",), {}))
    add("list", _cmd_list, (("--state",), {"default": None}))
    add(
        "resolve",
        _cmd_resolve,
        (("kind",), {}),
        (("--requires",), {"default": None}),
        (("--languages",), {"default": None}),
    )
    add("load", _cmd_load, (("id",), {}))
    add("unload", _cmd_unload, (("id",), {}))
    add("remove", _cmd_remove, (("id",), {}))
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ModelManagerError, ModelSourceError, HashMismatchError, CatalogError, StorageError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
