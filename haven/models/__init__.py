"""The HAVEN Model Manager: a capability router, not a model catalog.

Models are interchangeable local capabilities, not the product. Every
family -- intelligence, speech, vision, embeddings, prediction,
specialized -- enters through the same three paths (Download, Load Local,
Register External), is described by one manifest schema, and is resolved by
kind + capabilities + languages, never by name, architecture, or backend.
Backends are deployer-registered plugins; HAVEN Core ships none, so a load
with no loader for a manifest's backend fails with the explicit state
BACKEND_MISSING. Multiple models are loaded simultaneously by design.
"""

from .backends import BackendRegistry, LoadedModel, ModelBackend
from .catalog import CatalogEntry, CatalogError, default_catalog, load_catalog
from .contracts import (
    ModelDescriptor,
    ModelKind,
    ModelSource,
    descriptor_from_dict,
    descriptor_to_dict,
)
from .discovery import DiscoveryResult, inspect_folder, scan_roots
from .downloader import (
    HashMismatchError,
    ModelSourceError,
    UrlInspection,
    download_files,
    inspect_manifest_url,
)
from .integrity import hash_file, verify_files
from .manager import (
    BackendMissingError,
    ModelLoadError,
    ModelManager,
    ModelManagerError,
    ModelNotFoundError,
)
from .manifest import SCHEMA_VERSION, ManifestError, ModelManifest, manifest_filename
from .registry import ModelRecord, ModelRegistry, RegistryError
from .states import ModelState
from .storage import ModelStorage, StorageError, default_models_root

__all__ = [
    "BackendMissingError",
    "BackendRegistry",
    "CatalogEntry",
    "CatalogError",
    "DiscoveryResult",
    "HashMismatchError",
    "LoadedModel",
    "ManifestError",
    "ModelBackend",
    "ModelDescriptor",
    "ModelKind",
    "ModelLoadError",
    "ModelManager",
    "ModelManagerError",
    "ModelManifest",
    "ModelNotFoundError",
    "ModelRecord",
    "ModelRegistry",
    "ModelSource",
    "ModelSourceError",
    "ModelState",
    "ModelStorage",
    "RegistryError",
    "SCHEMA_VERSION",
    "StorageError",
    "UrlInspection",
    "default_catalog",
    "default_models_root",
    "descriptor_from_dict",
    "descriptor_to_dict",
    "download_files",
    "hash_file",
    "inspect_folder",
    "inspect_manifest_url",
    "load_catalog",
    "manifest_filename",
    "scan_roots",
    "verify_files",
]
