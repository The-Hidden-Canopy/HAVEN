"""The HAVEN Model Manager: a capability router, not a model catalog.

Models are interchangeable local capabilities, not the product. Every
family -- intelligence, speech, vision, embeddings, prediction,
specialized -- enters through the same three paths (Download, Load Local,
Register External), is described by one manifest schema, and is resolved by
kind + capabilities + languages, never by name, architecture, or backend.
Backends are plugins. HAVEN ships a reference set
(`haven.models.backends.reference`): the "http" backend is fully real on the
standard library, and the "transformers" / "llama_cpp" / "onnx" backends are
lazy -- they activate only when their runtime is importable and surface as
BACKEND_MISSING otherwise, never as LOAD_FAILED. Community backends
(mlx, rocm, coreml, ...) register the same way. A load with no loader for a
manifest's backend fails with the explicit state BACKEND_MISSING. Multiple
models are loaded simultaneously by design.
"""

from .backends import BackendRegistry, LoadedModel, ModelBackend
from .catalog import CatalogEntry, CatalogError, default_catalog, load_catalog
from .contracts import (
    InvalidModelIdError,
    ModelDescriptor,
    ModelKind,
    ModelSource,
    descriptor_from_dict,
    descriptor_to_dict,
    safe_model_id,
)
from .detect import (
    CheckpointTooLargeError,
    Detection,
    detect_folder,
    slugify_model_id,
    synthesize_from_files,
)
from .discovery import DiscoveryResult, inspect_folder, scan_roots
from .downloader import (
    DownloadCancelledError,
    HashMismatchError,
    ModelSourceError,
    ResolvedSource,
    UrlInspection,
    download_files,
    inspect_manifest_url,
    resolve_model_url,
)
from .integrity import hash_file, verify_files
from .jobs import DownloadJob, DownloadJobManager, JobState, job_to_dict
from .manager import (
    ROLE_ASR,
    ROLE_CHAT,
    ROLE_REQUIREMENTS,
    ROLE_TTS,
    ROLE_VAD,
    ROLE_VISION,
    ROLE_WAKE_WORD,
    ROLES,
    BackendAvailability,
    BackendMissingError,
    ModelLoadError,
    ModelManager,
    ModelManagerError,
    ModelNotFoundError,
)
from .manifest import SCHEMA_VERSION, ManifestError, ModelManifest, manifest_filename
from .registry import ModelRecord, ModelRegistry, RegistryError
from .results import ChatResult, InferenceResult
from .states import ModelState
from .storage import ModelStorage, StorageError, default_models_root

__all__ = [
    "BackendAvailability",
    "BackendMissingError",
    "BackendRegistry",
    "CatalogEntry",
    "CatalogError",
    "CheckpointTooLargeError",
    "ChatResult",
    "Detection",
    "DiscoveryResult",
    "DownloadCancelledError",
    "DownloadJob",
    "DownloadJobManager",
    "HashMismatchError",
    "InferenceResult",
    "InvalidModelIdError",
    "JobState",
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
    "ResolvedSource",
    "ROLE_ASR",
    "ROLE_CHAT",
    "ROLE_REQUIREMENTS",
    "ROLE_TTS",
    "ROLE_VAD",
    "ROLE_VISION",
    "ROLE_WAKE_WORD",
    "ROLES",
    "SCHEMA_VERSION",
    "StorageError",
    "UrlInspection",
    "default_catalog",
    "default_models_root",
    "descriptor_from_dict",
    "descriptor_to_dict",
    "detect_folder",
    "download_files",
    "hash_file",
    "inspect_folder",
    "inspect_manifest_url",
    "job_to_dict",
    "load_catalog",
    "manifest_filename",
    "resolve_model_url",
    "safe_model_id",
    "scan_roots",
    "slugify_model_id",
    "synthesize_from_files",
    "verify_files",
]
