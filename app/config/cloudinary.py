from __future__ import annotations

import logging
from typing import Any

import cloudinary
import cloudinary.uploader
from cloudinary import CloudinaryImage

from app.config.settings import settings

logger = logging.getLogger(__name__)


# Cloudinary SDK configuration loaded from the central settings module.
cloudinary.config(
    cloud_name=settings.cloudinary_cloud_name,
    api_key=settings.cloudinary_api_key,
    api_secret=settings.cloudinary_api_secret.get_secret_value() if settings.cloudinary_api_secret else None,
)


def upload_image(file_path: str, public_id: str | None = None, folder: str | None = None) -> dict[str, Any]:
    """Upload an image to Cloudinary and return the API response payload."""
    try:
        options: dict[str, Any] = {}
        if public_id:
            options["public_id"] = public_id
        if folder:
            options["folder"] = folder
        return cloudinary.uploader.upload(file_path, **options)
    except Exception as exc:
        logger.exception("Cloudinary upload failed: %s", exc)
        raise RuntimeError("Cloudinary upload failed") from exc


def delete_image(public_id: str) -> dict[str, Any]:
    """Delete an image from Cloudinary by its public ID."""
    try:
        return cloudinary.uploader.destroy(public_id)
    except Exception as exc:
        logger.exception("Cloudinary deletion failed: %s", exc)
        raise RuntimeError("Cloudinary deletion failed") from exc


def build_image_url(public_id: str, version: int | None = None) -> str:
    """Build a Cloudinary image URL from a public ID."""
    image = CloudinaryImage(public_id)
    if version is not None:
        image = CloudinaryImage(public_id, version=version)
    return image.url
