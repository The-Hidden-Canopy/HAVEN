"""Create a local, manifested Windows package from a clean reviewed commit.

No release, upload, signing, pip install or live-state access. Existing output
is never replaced. PythonHome is an explicitly selected stdlib-only runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from importlib.metadata import distributions


def extract_archive(archive: bytes, destination: Path):
    """Extract only regular tracked files beneath the selected new root."""
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for entry in files.getmembers():
            path = destination / entry.name
            if not path.resolve().is_relative_to(destination) or not (entry.isdir() or entry.isfile()):
                raise ValueError("Unexpected source archive member.")
            if entry.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(files.extractfile(entry).read())


def copy_crypto_runtime(python_root: Path, output: Path):
    """Copy only the existing signature-verifier dependency closure, offline."""
    site = python_root / "Lib/site-packages"
    installed = {d.metadata["Name"].lower(): d for d in distributions(path=[str(site)])}
    versions = {}
    for name in ("cryptography", "cffi", "pycparser"):
        distribution = installed.get(name)
        if distribution is None:
            raise ValueError("Selected runtime lacks the signature-verifier dependencies.")
        versions[name] = distribution.version
        for entry in distribution.files or ():
            relative = Path(entry)
            # Exclude installers, cache, tests and provenance URLs. Keep
            # runtime code, binaries, metadata and upstream licenses only.
            if any(p in {"..", "__pycache__", "test", "tests"} for p in relative.parts):
                continue
            if relative.suffix in {".pyc", ".pyo"} or relative.name in {"direct_url.json", "INSTALLER", "REQUESTED", "RECORD"}:
                continue
            first = relative.parts[0]
            if first != name and not first.startswith(name + "-") and not (name == "cffi" and first.startswith("_cffi_backend.") and first.endswith(".pyd")):
                continue
            source = site / relative
            if not source.is_file() or source.is_symlink() or any(
                getattr(p.lstat(), "st_file_attributes", 0) & 0x400 for p in (source, *source.parents) if p.exists()
            ):
                raise ValueError("Signature-verifier dependency contains an unsafe path.")
            target = output / "runtime/packages" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return versions


def copy_dependency_notices(source: Path, output: Path):
    """Retain resolved NuGet licenses and notices without copying build metadata."""
    packages = {}
    for project in ("Haven.Desktop", "Haven.Launcher"):
        assets = json.loads((source / "native" / project / "obj/project.assets.json").read_text(encoding="utf-8"))
        roots = [Path(p) for p in assets["packageFolders"]]
        for identity, record in assets["libraries"].items():
            if record.get("type") != "package" or identity in packages:
                continue
            relative = Path(record["path"])
            if relative.anchor or ".." in relative.parts:
                raise ValueError("Unsafe dependency package path.")
            package = next((p / relative for p in roots if (p / relative).is_dir()), None)
            if package is None or package.is_symlink():
                raise ValueError("Resolved dependency package is unavailable.")
            selected = {p for p in package.rglob("*") if p.is_file() and p.name.upper().startswith(("LICENSE", "LICENCE", "NOTICE", "THIRDPARTY", "THIRD-PARTY", "COPYING"))}
            terms = []
            for spec in package.glob("*.nuspec"):
                for node in ET.parse(spec).getroot().iter():
                    if node.tag.rsplit("}", 1)[-1] != "license":
                        continue
                    value = (node.text or "").strip()
                    terms.append({"type": node.attrib.get("type"), "value": value})
                    if node.attrib.get("type") == "file":
                        license_path = Path(value)
                        if license_path.anchor or ".." in license_path.parts:
                            raise ValueError("Unsafe dependency license path.")
                        selected.add(package / license_path)
            copied = []
            for file in sorted(selected):
                if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(package.resolve()):
                    raise ValueError("Unsafe dependency notice input.")
                relative_file = file.relative_to(package)
                target = output / "licenses/nuget" / relative / relative_file
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
                copied.append(target.relative_to(output).as_posix())
            packages[identity] = {"licenses": terms, "notices": copied}
    target = output / "licenses/nuget/packages.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(packages, indent=2) + "\n", encoding="utf-8")
    return packages


def build(output: Path, python_root: Path):
    repo = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if not output.is_relative_to(repo) or output == repo or output.exists():
        raise ValueError("Output must be a new directory inside this checkout.")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repo).strip():
        raise ValueError("Build requires a clean reviewed source commit.")
    if subprocess.run(["git", "check-ignore", "--quiet", str(output)], cwd=repo).returncode:
        raise ValueError("Build output must be locally ignored.")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    python_root = python_root.resolve()
    if not all((python_root / name).is_file() for name in ("python.exe", "pythonw.exe")):
        raise ValueError("Selected Python runtime is incomplete.")
    work = output.parent / (output.name + "-build")
    if work.exists():
        raise ValueError("Build workspace already exists; select another output name.")
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    source = work / "source"
    source.mkdir()
    archive = subprocess.check_output(["git", "archive", "HEAD", "native", "haven"], cwd=repo)
    extract_archive(archive, source)
    env = dict(os.environ)
    env.update(HOME=str(work / "home"), USERPROFILE=str(work / "home"),
               APPDATA=str(work / "home/roaming"), LOCALAPPDATA=str(work / "home/local"),
               TEMP=str(work / "temp"), TMP=str(work / "temp"),
               DOTNET_CLI_HOME=str(work / "dotnet"), NUGET_HTTP_CACHE_PATH=str(work / "http-cache"),
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", DOTNET_GENERATE_ASPNET_CERTIFICATE="false",
               DOTNET_ADD_GLOBAL_TOOLS_TO_PATH="false", DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
    for key in ("HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "DOTNET_CLI_HOME", "NUGET_HTTP_CACHE_PATH"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    # Restore from existing caches with all remote package sources removed.
    # Compilation uses the exact Git archive, including no ignored C# inputs.
    original_profile = os.environ.get("USERPROFILE", "")
    cache = repo / ".haven-local/nuget"
    cache.mkdir(parents=True, exist_ok=True)
    env["NUGET_PACKAGES"] = str(cache)
    fallback = Path(original_profile) / ".nuget/packages"
    empty_feed = work / "empty-feed"
    empty_feed.mkdir()
    configuration = ET.Element("configuration")
    sources = ET.SubElement(configuration, "packageSources")
    ET.SubElement(sources, "clear")
    ET.SubElement(sources, "add", key="offline-local", value=str(empty_feed))
    nuget_config = work / "offline-nuget.config"
    ET.ElementTree(configuration).write(nuget_config, encoding="utf-8", xml_declaration=True)
    common = ["-p:SourceRevisionId=" + commit, "-p:DebugType=None", "-p:DebugSymbols=false",
              "-p:UseSharedCompilation=false", "-p:BuildInParallel=false", "-p:PathMap=" + str(source) + "=/_/HAVEN"]

    def run(arguments, log):
        with (work / log).open("w", encoding="utf-8") as stream:
            result = subprocess.run(arguments, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError("Build failed; inspect the private build log.")

    for project in ("Haven.Desktop", "Haven.Launcher"):
        run(["dotnet", "restore", f"native/{project}/{project}.csproj", "--configfile", str(nuget_config),
             "-p:Platform=x64", "-p:RuntimeIdentifiers=win-x64", "-p:PublishProfile=", "-p:SelfContained=true",
             "-p:RestoreFallbackFolders=" + str(fallback), "-p:UserProfile=" + original_profile], project + "-restore.log")
    run(["dotnet", "publish", "native/Haven.Desktop/Haven.Desktop.csproj", "--no-restore", "-c", "Release", "-r", "win-x64", "--self-contained", "true",
         "-p:Platform=x64", "-p:UserProfile=" + original_profile, "-p:PublishProfile=", "-p:PublishReadyToRun=false",
         "-p:PublishDir=" + str(output / "native") + "\\", "-p:XamlGeneratedOutputPath=" + str(source / "native/Haven.Desktop/obj/x64/Release/net8.0-windows10.0.19041.0") + "\\", *common], "native.log")
    run(["dotnet", "publish", "native/Haven.Launcher/Haven.Launcher.csproj", "--no-restore", "-c", "Release", "-r", "win-x64", "--self-contained", "true",
         "-p:PublishDir=" + str(work / "launcher") + "\\", *common], "launcher.log")
    shutil.copyfile(work / "launcher/Haven.Launcher.exe", output / "Haven.exe")
    archive = subprocess.check_output(["git", "archive", "HEAD", "haven", "docs", "README.md", "LICENSE", "pyproject.toml"], cwd=repo)
    extract_archive(archive, output)
    for source in sorted(python_root.rglob("*")):
        relative = source.relative_to(python_root)
        if not source.is_file() or any(part in {"__pycache__", "site-packages", "test", "tests", "idle_test", "ensurepip", "idlelib", "turtledemo", "venv"} for part in relative.parts) or source.suffix in {".pyc", ".pyo"}:
            continue
        # Only runtime root files and the two standard runtime trees.
        if len(relative.parts) > 1 and relative.parts[0] not in {"Lib", "DLLs"}:
            continue
        if len(relative.parts) == 1 and source.name not in {"python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"}:
            continue
        if source.is_symlink() or getattr(source.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Runtime input contains an unsupported link.")
        target = output / "runtime" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    dependencies = copy_crypto_runtime(python_root, output)
    dotnet_packages = copy_dependency_notices(work / "source", output)
    forbidden = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx", ".pdb", ".pyc", ".log", ".patch", ".diff", ".gguf", ".safetensors", ".onnx", ".pt", ".pth"}
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            relative = path.relative_to(output).as_posix()
            if path.suffix.lower() in forbidden or any(part in {".haven-local", ".git", "__pycache__"} for part in path.relative_to(output).parts):
                raise ValueError("Unexpected private/generated artifact in package.")
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"schema_version": 1, "source_commit": commit, "architecture": "win-x64", "dependencies": dependencies, "dotnet_packages": dotnet_packages, "publication": "unsigned preview; provider/device acceptance remains separate", "files": files}
    (output / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.output, args.python_root)
    print(json.dumps({"source_commit": manifest["source_commit"], "payload_files": len(manifest["files"])}))
