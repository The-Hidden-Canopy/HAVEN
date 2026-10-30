"""Public packages retain upstream terms and cannot copy outside a dependency."""
import json
from pathlib import Path
import runpy
import pytest

copy_notices = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build_windows.py"))["copy_dependency_notices"]

def assets(source, cache, package_path="example/1.0"):
    for name in ("Haven.Desktop", "Haven.Launcher"):
        path = source / "native" / name / "obj/project.assets.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"packageFolders": {str(cache): {}}, "libraries": {"Example/1.0": {"type": "package", "path": package_path}}}))

def test_upstream_notice_and_declared_license_are_retained(tmp_path):
    cache = tmp_path / "cache"
    package = cache / "example/1.0"
    package.mkdir(parents=True)
    (package / "terms.txt").write_text("Upstream copyright and terms.\n")
    (package / "NOTICE").write_text("Original contributor notice.\n")
    (package / "example.nuspec").write_text('<package><metadata><license type="file">terms.txt</license></metadata></package>')
    source, output = tmp_path / "source", tmp_path / "output"
    assets(source, cache)
    result = copy_notices(source, output)
    assert (output / "licenses/nuget/example/1.0/terms.txt").read_text() == "Upstream copyright and terms.\n"
    assert (output / "licenses/nuget/example/1.0/NOTICE").read_text() == "Original contributor notice.\n"
    assert result["Example/1.0"]["licenses"] == [{"type": "file", "value": "terms.txt"}]

@pytest.mark.parametrize("escape", ["package", "license"])
def test_dependency_metadata_cannot_export_an_external_file(tmp_path, escape):
    cache = tmp_path / "cache"
    package = cache / "example/1.0"
    package.mkdir(parents=True)
    (package / "example.nuspec").write_text('<package><metadata><license type="file">../../outside.txt</license></metadata></package>')
    source, output = tmp_path / "source", tmp_path / "output"
    assets(source, cache, "../outside" if escape == "package" else "example/1.0")
    with pytest.raises(ValueError, match="Unsafe dependency"):
        copy_notices(source, output)
    assert not output.exists()
