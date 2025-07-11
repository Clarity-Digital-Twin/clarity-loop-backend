"""PAT Model Loader Component - Following SOLID principles.

Responsible for loading, caching, and versioning PAT models.
Extracted from monolithic PATService for better separation of concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import logging
from pathlib import Path
import time
from typing import Any, cast

import torch
from torch import nn

from clarity.ml.pat_architecture import ModelConfig as ArchModelConfig
from clarity.ml.pat_architecture import PATModel
from clarity.monitoring import (
    record_cache_hit,
    record_cache_miss,
    record_fallback_attempt,
    record_s3_download,
    record_validation_attempt,
    track_checksum_verification,
    track_model_load,
    update_cache_metrics,
    update_current_version,
    update_loading_progress,
)
from clarity.services.s3_storage_service import S3StorageService

logger = logging.getLogger(__name__)


class ModelSize(StrEnum):
    """PAT model size variants."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


def get_model_config(size: ModelSize) -> ArchModelConfig:
    """Get configuration for specific model size.

    Based on Dartmouth specifications from arXiv:2411.15240
    """
    configs = {
        ModelSize.SMALL: ArchModelConfig(
            name="PAT-S",
            input_size=10080,  # 7 days at 1 minute intervals
            patch_size=18,
            embed_dim=96,
            num_layers=1,
            num_heads=6,
            ff_dim=256,
            dropout=0.1,
        ),
        ModelSize.MEDIUM: ArchModelConfig(
            name="PAT-M",
            input_size=10080,
            patch_size=18,
            embed_dim=96,
            num_layers=2,
            num_heads=12,
            ff_dim=256,
            dropout=0.1,
        ),
        ModelSize.LARGE: ArchModelConfig(
            name="PAT-L",
            input_size=10080,
            patch_size=9,
            embed_dim=96,
            num_layers=4,
            num_heads=12,
            ff_dim=256,
            dropout=0.1,
        ),
    }
    return configs[size]


@dataclass
class ModelVersion:
    """Model version information."""

    version: str
    timestamp: float
    checksum: str
    size: ModelSize
    metrics: dict[str, float]  # Performance metrics for this version


class ModelLoadError(Exception):
    """Error loading model."""


class ModelCache:
    """Simple in-memory model cache with TTL.

    Follows Single Responsibility: Only caches models.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        """Initialize cache with TTL."""
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Any | None:
        """Get model from cache if not expired."""
        if key not in self._cache:
            return None

        model, timestamp = self._cache[key]
        if time.time() - timestamp > self._ttl:
            del self._cache[key]
            return None

        return model

    def set(self, key: str, model: Any) -> None:
        """Store model in cache."""
        self._cache[key] = (model, time.time())

    def clear(self) -> None:
        """Clear all cached models."""
        self._cache.clear()

    def size(self) -> int:
        """Get number of cached models."""
        return len(self._cache)


class PATModelLoader:
    """Loads and manages PAT models with versioning and caching.

    Follows SOLID principles:
    - Single Responsibility: Only loads and caches models
    - Open/Closed: Extensible for new model types
    - Dependency Inversion: Depends on abstractions (S3StorageService)
    """

    def __init__(
        self,
        model_dir: Path,
        s3_service: S3StorageService | None = None,
        cache_ttl: int = 3600,
        *,
        enable_hot_swap: bool = False,
    ) -> None:
        """Initialize model loader.

        Args:
            model_dir: Local directory for model files
            s3_service: Optional S3 service for remote model storage
            cache_ttl: Cache time-to-live in seconds
            enable_hot_swap: Enable hot-swapping of models
        """
        self.model_dir = model_dir
        self.s3_service = s3_service
        self._cache = ModelCache(cache_ttl)
        self.enable_hot_swap = enable_hot_swap

        # Current loaded versions
        self._current_versions: dict[ModelSize, ModelVersion] = {}

        # Model loading metrics
        self._load_times: list[float] = []

        logger.info(
            "PATModelLoader initialized with model_dir=%s, cache_ttl=%s",
            model_dir,
            cache_ttl,
        )

    async def load_model(
        self, size: ModelSize, version: str | None = None, *, force_reload: bool = False
    ) -> nn.Module:
        """Load a PAT model with specified size and version.

        Args:
            size: Model size (small, medium, large)
            version: Specific version to load (latest if None)
            force_reload: Force reload even if cached

        Returns:
            Loaded PyTorch model

        Raises:
            ModelLoadError: If model cannot be loaded
        """
        start_time = time.time()
        version_str = version or "latest"

        # Track model load operation
        async with track_model_load(
            size.value, version_str, "cache" if not force_reload else "local"
        ) as ctx:
            try:
                # Check cache first
                cache_key = f"{size.value}:{version_str}"
                if not force_reload:
                    cached_model = self._cache.get(cache_key)
                    if cached_model is not None:
                        logger.debug("Model loaded from cache: %s", cache_key)
                        record_cache_hit(size.value, version_str)
                        ctx["success"] = True
                        ctx["source"] = "cache"
                        # Update cache metrics
                        self._update_cache_metrics()
                        return cast(nn.Module, cached_model)
                    record_cache_miss(size.value, version_str)

                # Update loading progress
                update_loading_progress(size.value, version_str, "init", 0.1)

                # Load model configuration
                config = get_model_config(size)

                # Determine model path
                if version:
                    model_path = self._get_versioned_path(size, version)
                else:
                    model_path = self._get_latest_path(size)

                # Load from S3 if configured and file doesn't exist locally
                if self.s3_service and not model_path.exists():
                    ctx["source"] = "s3"
                    update_loading_progress(size.value, version_str, "download", 0.2)
                    await self._download_from_s3(size, version, model_path)
                    update_loading_progress(size.value, version_str, "download", 0.5)
                else:
                    ctx["source"] = "local"

                # Verify checksum
                update_loading_progress(size.value, version_str, "checksum", 0.6)
                await self._verify_checksum(size, version_str, model_path)
                update_loading_progress(size.value, version_str, "checksum", 0.7)

                # Load model weights
                update_loading_progress(size.value, version_str, "load", 0.8)
                model = self._load_model_weights(config, model_path)

                # Validate model
                update_loading_progress(size.value, version_str, "validate", 0.9)
                self._validate_model(model, config)

                # Cache the model
                self._cache.set(cache_key, model)

                # Update current version
                self._update_current_version(size, version_str, model_path)

                # Record metrics
                load_time = time.time() - start_time
                self._load_times.append(load_time)

                # Update cache metrics
                self._update_cache_metrics()

                # Mark as successful and complete
                ctx["success"] = True
                update_loading_progress(size.value, version_str, "complete", 1.0)

                logger.info(
                    "Model loaded successfully: %s (%.2fs)", cache_key, load_time
                )

                return model

            except Exception as e:
                logger.exception("Failed to load model: %s", size.value)
                ctx["error_type"] = type(e).__name__
                msg = f"Failed to load {size.value} model: {e}"
                raise ModelLoadError(msg) from e

    def _get_versioned_path(self, size: ModelSize, version: str) -> Path:
        """Get path for specific model version."""
        return self.model_dir / f"pat_{size.value}_v{version}.pth"

    def _get_latest_path(self, size: ModelSize) -> Path:
        """Get path for latest model version."""
        # Look for highest version number
        pattern = f"pat_{size.value}_v*.pth"
        versions = sorted(self.model_dir.glob(pattern))

        if not versions:
            # Fallback to default name
            return self.model_dir / f"pat_{size.value}.pth"

        return versions[-1]

    async def _download_from_s3(
        self, size: ModelSize, version: str | None, local_path: Path
    ) -> None:
        """Download model from S3."""
        if not self.s3_service:
            msg = "S3 service not configured"
            raise ModelLoadError(msg)

        s3_key = f"models/pat/{size.value}/v{version or 'latest'}.pth"
        version_str = version or "latest"

        logger.info("Downloading model from S3: %s", s3_key)

        # Ensure directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Download from S3
        download_start = time.time()
        try:
            file_data = await self.s3_service.download_file(s3_key)

            # Write to local file
            local_path.write_bytes(file_data)

            # Record successful download
            download_duration = time.time() - download_start
            record_s3_download(
                size.value, version_str, "success", download_duration, len(file_data)
            )

        except Exception as e:
            # Record failed download
            download_duration = time.time() - download_start
            record_s3_download(size.value, version_str, "failed", download_duration)

            msg = f"Failed to download model from S3: {s3_key}"
            raise ModelLoadError(msg) from e

    def _load_model_weights(
        self, config: ArchModelConfig, model_path: Path
    ) -> nn.Module:
        """Load model weights from file."""
        if not model_path.exists():
            msg = f"Model file not found: {model_path}"
            raise ModelLoadError(msg)

        # Create model architecture
        model = PATModel(config)

        # Load weights
        try:
            state_dict = torch.load(
                model_path, map_location=torch.device("cpu"), weights_only=True
            )
            model.load_state_dict(state_dict)
            model.eval()  # Set to evaluation mode

        except Exception as e:
            msg = f"Failed to load weights: {e}"
            raise ModelLoadError(msg) from e

        return model

    def _validate_model(self, model: nn.Module, config: ArchModelConfig) -> None:
        """Validate loaded model matches expected configuration."""
        # Run a test forward pass
        try:
            with torch.no_grad():
                test_input = torch.randn(1, config.input_size)
                output = model(test_input)

                # Validate output shape
                expected_patches = config.input_size // config.patch_size
                if output.shape != (1, expected_patches, config.embed_dim):
                    msg = f"Invalid output shape: {output.shape}"
                    raise ModelLoadError(msg)

            # Record successful validation
            record_validation_attempt(
                config.name.lower().replace("pat-", ""), "forward_pass", success=True
            )

        except Exception as e:
            # Record failed validation
            record_validation_attempt(
                config.name.lower().replace("pat-", ""),
                "forward_pass",
                success=False,
                error_type=type(e).__name__,
            )
            msg = f"Model validation failed: {e}"
            raise ModelLoadError(msg) from e

    async def _verify_checksum(
        self, size: ModelSize, version: str, model_path: Path
    ) -> None:
        """Verify model checksum for security."""
        async with track_checksum_verification(size.value, version) as ctx:
            try:
                # Calculate actual checksum
                actual_checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()
                ctx["actual_checksum"] = actual_checksum

                # In a real implementation, you would fetch expected checksum from a secure source
                # For now, we'll just log the checksum
                logger.info(
                    "Model checksum for %s v%s: %s",
                    size.value,
                    version,
                    actual_checksum,
                )

                # TODO: Implement actual checksum verification against known good values

                ctx["success"] = True

            except Exception:
                ctx["success"] = False
                raise

    def _update_current_version(
        self, size: ModelSize, version: str, model_path: Path
    ) -> None:
        """Update current version tracking."""
        # Calculate checksum
        checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()

        self._current_versions[size] = ModelVersion(
            version=version,
            timestamp=time.time(),
            checksum=checksum,
            size=size,
            metrics={},  # To be populated by monitoring
        )

        # Update metrics
        update_current_version(size.value, version, checksum)

    async def fallback_to_previous(self, size: ModelSize) -> nn.Module:
        """Fallback to previous model version on failure.

        Implements graceful degradation.
        """
        current = self._current_versions.get(size)
        if not current:
            msg = "No current version to fallback from"
            raise ModelLoadError(msg)

        # Extract version number
        try:
            current_num = int(current.version.replace("v", ""))
            previous_version = f"{current_num - 1}"  # Don't include "v" prefix

            logger.warning(
                "Falling back from %s to v%s", current.version, previous_version
            )

            # Record fallback attempt
            record_fallback_attempt(
                size.value,
                current.version,
                f"v{previous_version}",
                False,  # Not yet successful
            )

            model = await self.load_model(size, previous_version)

            # Record successful fallback
            record_fallback_attempt(
                size.value, current.version, f"v{previous_version}", True
            )

            return model

        except Exception as e:
            msg = f"Fallback failed: {e}"
            raise ModelLoadError(msg) from e

    def get_metrics(self) -> dict[str, Any]:
        """Get model loading metrics."""
        if not self._load_times:
            return {}

        return {
            "average_load_time_ms": sum(self._load_times)
            / len(self._load_times)
            * 1000,
            "total_loads": len(self._load_times),
            "cached_models": self._cache.size(),
            "current_versions": {
                size.value: version.version
                for size, version in self._current_versions.items()
            },
        }

    def clear_cache(self) -> None:
        """Clear model cache."""
        self._cache.clear()
        logger.info("Model cache cleared")
        self._update_cache_metrics()

    def get_current_version(self, size: ModelSize) -> ModelVersion | None:
        """Get current version info for a model size."""
        return self._current_versions.get(size)

    def _update_cache_metrics(self) -> None:
        """Update cache-related metrics."""
        cache_size = self._cache.size()

        # Estimate memory usage (simplified - in production would be more accurate)
        estimated_memory = cache_size * 256 * 1024 * 1024  # Assume 256MB per model

        update_cache_metrics(cache_size, estimated_memory)
