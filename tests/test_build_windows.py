"""Packaging rejects injected archive paths and links before compilation."""
import io
from pathlib import Path
import runpy
import tarfile

import pytest


extract = runpy.run_path(str(Path(__file__).parents[1] / "scripts/build_windows.py"))["extract_archive"]


@pytest.mark.parametrize("name,kind", [("../outside.txt", "file"), ("source/link", "symlink"), ("source/link", "hardlink")])
def test_source_archive_cannot_escape_or_import_link_targets(tmp_path, name, kind):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as stream:
        member = tarfile.TarInfo(name)
        if kind != "file":
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "../outside.txt"
        else:
            member.size = 5
        stream.addfile(member, io.BytesIO(b"inert") if kind == "file" else None)
    target = tmp_path / "package"
    target.mkdir()
    with pytest.raises(ValueError, match="Unexpected source archive"):
        extract(archive.getvalue(), target)
    assert not (tmp_path / "outside.txt").exists()
