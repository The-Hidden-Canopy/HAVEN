"""Discovery classifies candidates and NEVER activates anything: every
outcome (unsupported, incomplete, hash mismatch, license unknown,
inspected) is a flag on a result, not a registry write.
"""

import hashlib
import tempfile
from pathlib import Path

from haven.models import ModelState, scan_roots
from haven.models.manifest import ModelManifest, manifest_filename

from models_stub_http import make_manifest_dict


def _folder(root: Path, name: str, files: dict[str, bytes], manifest_overrides=None) -> Path:
    manifest = make_manifest_dict(name, **(manifest_overrides or {}))
    folder = root / name
    folder.mkdir(parents=True)
    for rel, data in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    ModelManifest.from_dict(manifest).save(folder / manifest_filename())
    return folder


def _hashes(**files: bytes) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


# must match the digest baked into make_manifest_dict
WEIGHTS = b"local weights"


def test_inspected_when_everything_checks_out():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sha = _hashes(**{"weights.bin": WEIGHTS})
        _folder(root, "ok-model", {"weights.bin": WEIGHTS}, {"license_name": "MIT"})
        results = scan_roots([root])
        assert len(results) == 1
        assert results[0].state is ModelState.INSPECTED
        assert results[0].manifest.id == "ok-model"
        assert results[0].problems == ()


def test_bad_manifest_is_unsupported_with_the_problem_text():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / "broken"
        folder.mkdir()
        (folder / manifest_filename()).write_text("{ nope", encoding="utf-8")
        results = scan_roots([root])
        assert results[0].state is ModelState.UNSUPPORTED
        assert results[0].manifest is None
        assert any("JSON" in problem for problem in results[0].problems)


def test_missing_declared_file_is_incomplete():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sha = _hashes(**{"weights.bin": WEIGHTS, "extra.bin": b"x"})
        _folder(
            root,
            "partial",
            {"weights.bin": WEIGHTS},
            {"capabilities": ("chat",)},
        )
        # declare a second file in the manifest without writing it
        folder = root / "partial"
        manifest = ModelManifest.from_dict(make_manifest_dict("partial"))
        data = manifest.to_dict()
        data["files"]["extra"] = "extra.bin"
        data["sha256"]["extra.bin"] = hashlib.sha256(b"x").hexdigest()
        ModelManifest.from_dict(data).save(folder / manifest_filename())
        results = scan_roots([root])
        assert results[0].state is ModelState.INCOMPLETE
        assert any("extra.bin" in problem for problem in results[0].problems)


def test_wrong_bytes_are_hash_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _folder(root, "tampered", {"weights.bin": b"attacker bytes"})
        results = scan_roots([root])
        assert results[0].state is ModelState.HASH_MISMATCH
        assert any("weights.bin" in problem for problem in results[0].problems)


def test_empty_license_is_flagged_but_still_a_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _folder(root, "no-license", {"weights.bin": WEIGHTS}, {"license_name": None})
        results = scan_roots([root])
        assert results[0].state is ModelState.LICENSE_UNKNOWN
        assert results[0].manifest is not None
        assert results[0].problems


def test_folder_without_manifest_is_discovered_not_classified():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "mystery").mkdir()
        results = scan_roots([root])
        assert results[0].state is ModelState.DISCOVERED
        assert results[0].manifest is None


def test_scan_walks_immediate_subdirectories_only_and_skips_missing_roots():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "roots" / "r1"
        sha = _hashes(**{"weights.bin": WEIGHTS})
        _folder(root, "a-model", {"weights.bin": WEIGHTS})
        (root / "a-model" / "nested").mkdir()  # deeper levels are not walked
        (root / "nested" / "deep-model").mkdir(parents=True)
        (root / "file.txt").write_text("not a folder", encoding="utf-8")
        results = scan_roots([root, Path(tmp) / "does-not-exist"])
        by_name = {r.path.name: r.state for r in results}
        assert by_name["a-model"] is ModelState.INSPECTED
        # deeper levels are not walked; the intermediate folder is just a discovery
        assert "deep-model" not in by_name
        assert by_name["nested"] is ModelState.DISCOVERED


def test_scan_classifies_multiple_candidates_independently():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _folder(root, "good", {"weights.bin": WEIGHTS})
        _folder(root, "tampered", {"weights.bin": b"other"})
        _folder(root, "no-license", {"weights.bin": WEIGHTS}, {"license_name": None})
        states = {r.path.name: r.state for r in scan_roots([root])}
        assert states == {
            "good": ModelState.INSPECTED,
            "tampered": ModelState.HASH_MISMATCH,
            "no-license": ModelState.LICENSE_UNKNOWN,
        }


def test_scan_never_registers_anything():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _folder(root, "good", {"weights.bin": WEIGHTS})
        scan_roots([root])
        assert not (Path(tmp) / "registry.json").exists()
