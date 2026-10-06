"""Local draft assets compatible with the legacy Node file format."""

import io
import json
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import warnings

from PIL import Image
from PIL import ImageOps

from backend.errors import AppError

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
_MAX_BYTES = 10 * 1024 * 1024
_MAX_PIXELS = 80_000_000
_EXPIRY_MS = 24 * 60 * 60 * 1000


class AssetStore:
    """Manage local originals, previews, and atomic draft metadata writes."""

    def __init__(self, data_dir: Path) -> None:
        """Use an explicitly supplied local data directory.

        Args:
            data_dir: Local root containing legacy drafts and jobs directories.
        """
        self._data_dir = Path(data_dir)
        self._drafts_dir = self._data_dir / "drafts"
        self._lock = threading.RLock()

    def _draft_path(self, draft_id: str) -> Path:
        self._validate_id(draft_id, "草稿")
        return self._drafts_dir / draft_id

    @staticmethod
    def _validate_id(identifier: str, label: str) -> None:
        if not isinstance(identifier, str) or not _UUID.fullmatch(identifier):
            raise AppError(f"{label} ID 无效")

    @staticmethod
    def _asset_path(directory: Path, name: str, asset_id: str) -> Path:
        allowed = [f"{asset_id}.{ext}" for ext in ("jpg", "png", "webp")]
        allowed.append(f"{asset_id}.preview.webp")
        if name not in allowed:
            raise AppError("图片文件名无效")
        path = directory / name
        if path.is_symlink() or directory.is_symlink():
            raise AppError("图片路径无效")
        return path

    @staticmethod
    def _write_atomic(path: Path, content: bytes) -> None:
        temporary = path.parent / f"{uuid.uuid4()}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _save_draft(self, draft_id: str, draft: dict) -> None:
        content = json.dumps(draft, ensure_ascii=False).encode("utf-8")
        self._write_atomic(self._draft_path(draft_id) / "draft.json", content)

    def create_draft(self) -> dict:
        """Create a UUID draft with millisecond creation time.

        Returns:
            A dictionary with draftId containing the newly generated UUID.

        Raises:
            OSError: The draft directory or metadata cannot be written.
        """
        with self._lock:
            draft_id = str(uuid.uuid4())
            directory = self._draft_path(draft_id)
            directory.mkdir(parents=True)
            try:
                self._save_draft(
                    draft_id,
                    {
                        "id": draft_id,
                        "createdAt": time.time_ns() // 1_000_000,
                        "images": [],
                    },
                )
            except Exception:
                shutil.rmtree(directory)
                raise
            return {"draftId": draft_id}

    def read_draft(self, draft_id: str) -> dict:
        """Read legacy camelCase JSON; missing drafts return HTTP 404.

        Args:
            draft_id: UUID of the draft to read.

        Returns:
            The persisted dictionary with id, createdAt (milliseconds), and
            images. Each image has assetId, originalName, previewName,
            mimeType, and filename.

        Raises:
            AppError: The UUID or path is invalid (400), or the draft is
                missing (404).
            json.JSONDecodeError: Existing metadata is not valid JSON.
            OSError: Existing metadata cannot be read.
        """
        with self._lock:
            directory = self._draft_path(draft_id)
            if directory.is_symlink():
                raise AppError("草稿路径无效")
            try:
                return json.loads((directory / "draft.json").read_text("utf-8"))
            except FileNotFoundError as error:
                raise AppError("草稿不存在", 404) from error

    @staticmethod
    def _preview(content: bytes) -> tuple[bytes, str]:
        if not isinstance(content, bytes) or len(content) > _MAX_BYTES:
            raise AppError("每张图片不能超过 10 MB", 413)
        if not content:
            raise AppError("图片为空")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    format_name = image.format
                    if format_name not in _MIME:
                        raise AppError("仅支持 JPEG、PNG 或 WebP 图片")
                    if image.width * image.height > _MAX_PIXELS:
                        raise AppError("图片像素不能超过 8000 万")
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    # Decode every frame to reject truncated animations.
                    for frame in range(getattr(image, "n_frames", 1)):
                        image.seek(frame)
                        if image.width * image.height > _MAX_PIXELS:
                            raise AppError("图片像素不能超过 8000 万")
                        image.load()
                    image.seek(0)
                    oriented = ImageOps.exif_transpose(image)
                    ratio = min(400 / oriented.width, 400 / oriented.height)
                    size = (
                        max(1, round(oriented.width * ratio)),
                        max(1, round(oriented.height * ratio)),
                    )
                    preview = oriented.resize(size, Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    preview.save(output, "WEBP", quality=75)
                    return output.getvalue(), format_name
        except (
            OSError,
            ValueError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise AppError("图片内容无效或格式不受支持") from error

    def add_image(self, draft_id: str, content: bytes, filename: str) -> dict:
        """Validate fully, then commit unchanged originals and WebP previews.

        Args:
            draft_id: UUID of the destination draft.
            content: Original JPEG, PNG, or WebP bytes, at most 10 MiB and
                80 million decoded pixels per frame.
            filename: Display filename, truncated to 150 characters.

        Returns:
            A dictionary containing assetId, mimeType, and previewUrl.

        Raises:
            AppError: The draft is missing (404); its UUID/path, image format,
                decoding, pixel count, or six-image limit is invalid (400);
                or content is not bytes or exceeds 10 MiB (413).
            OSError: Assets or metadata cannot be written. New assets are
                removed before the error propagates.
        """
        with self._lock:
            draft = self.read_draft(draft_id)
            if len(draft["images"]) >= 6:
                raise AppError("同一产品最多上传 6 张图片")
            preview, format_name = self._preview(content)
            asset_id = str(uuid.uuid4())
            record = {
                "assetId": asset_id,
                "originalName": f"{asset_id}.{_EXT[format_name]}",
                "previewName": f"{asset_id}.preview.webp",
                "mimeType": _MIME[format_name],
                "filename": str(filename)[:150],
            }
            directory = self._draft_path(draft_id)
            paths = [
                directory / record[key]
                for key in ("originalName", "previewName")
            ]
            try:
                for path, payload in zip(paths, (content, preview)):
                    self._write_atomic(path, payload)
                draft["images"].append(record)
                self._save_draft(draft_id, draft)
            except Exception:
                for path in paths:
                    path.unlink(missing_ok=True)
                raise
            return {
                "assetId": asset_id,
                "mimeType": record["mimeType"],
                "previewUrl": (
                    f"/api/drafts/{draft_id}/images/{asset_id}/preview"
                ),
            }

    def _find_image(self, draft_id: str, asset_id: str) -> tuple[dict, dict]:
        self._validate_id(asset_id, "图片")
        draft = self.read_draft(draft_id)
        for record in draft["images"]:
            if record["assetId"] == asset_id:
                return draft, record
        raise AppError("图片不存在", 404)

    def read_image(
        self, draft_id: str, asset_id: str, preview: bool = False
    ) -> tuple[bytes, str]:
        """Return original or preview bytes and the legacy MIME type.

        Args:
            draft_id: UUID of the draft containing the image.
            asset_id: UUID of the image record.
            preview: Whether to read the WebP preview instead of the original.

        Returns:
            A (content bytes, MIME type) tuple. Preview MIME is image/webp.

        Raises:
            AppError: An ID, asset filename, or path is invalid (400), or the
                draft, image record, or referenced file is missing (404).
            OSError: An existing image file cannot be read.
        """
        with self._lock:
            _, record = self._find_image(draft_id, asset_id)
            key = "previewName" if preview else "originalName"
            path = self._asset_path(
                self._draft_path(draft_id), record[key], asset_id
            )
            try:
                return (
                    path.read_bytes(),
                    "image/webp" if preview else record["mimeType"],
                )
            except FileNotFoundError as error:
                raise AppError("图片不存在", 404) from error

    def remove_image(self, draft_id: str, asset_id: str) -> None:
        """Remove metadata first and then the referenced local asset files.

        Args:
            draft_id: UUID of the draft containing the image.
            asset_id: UUID of the image record to remove.

        Raises:
            AppError: An ID, filename, or path is invalid (400), or the draft
                or image record is missing (404).
            OSError: Metadata cannot be saved or asset files cannot be removed.
        """
        with self._lock:
            draft, record = self._find_image(draft_id, asset_id)
            paths = [
                self._asset_path(
                    self._draft_path(draft_id), record[key], asset_id
                )
                for key in ("originalName", "previewName")
            ]
            draft["images"].remove(record)
            self._save_draft(draft_id, draft)
            for path in paths:
                path.unlink(missing_ok=True)

    def copy_to_job(self, draft_id: str, job_id: str) -> list[dict]:
        """Copy both asset variants into a job's legacy originals directory.

        Args:
            draft_id: UUID of the source draft.
            job_id: UUID of the destination job.

        Returns:
            The draft's image record list. Records retain assetId, originalName,
            previewName, mimeType, and filename, and both variants retain bytes.

        Raises:
            AppError: An ID, filename, or path is invalid, or an existing
                destination differs from its source (400); draft missing (404).
            OSError: A source cannot be read or destination cannot be written.
                Files created by this call are removed before propagation.
        """
        with self._lock:
            self._validate_id(job_id, "任务")
            draft = self.read_draft(draft_id)
            directory = self._data_dir / "jobs" / job_id / "originals"
            directory.mkdir(parents=True, exist_ok=True)
            written = []
            try:
                for record in draft["images"]:
                    self._validate_id(record["assetId"], "图片")
                    for key in ("originalName", "previewName"):
                        source = self._asset_path(
                            self._draft_path(draft_id),
                            record[key],
                            record["assetId"],
                        )
                        target = self._asset_path(
                            directory, record[key], record["assetId"]
                        )
                        if target.exists():
                            if target.read_bytes() != source.read_bytes():
                                raise AppError("任务图片已存在")
                            continue
                        self._write_atomic(target, source.read_bytes())
                        written.append(target)
            except Exception:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            return draft["images"]

    def cleanup_expired(self, now_ms: int) -> int:
        """Delete only UUID drafts older than 24 hours; return removal count.

        Args:
            now_ms: Current Unix time in milliseconds for the expiry comparison.

        Returns:
            The number of draft directories removed. Exactly 24-hour-old drafts
            are retained, and job directories are unaffected.

        Raises:
            AppError: A candidate draft uses an invalid path (400).
            json.JSONDecodeError: Candidate metadata is not valid JSON.
            OSError: Drafts cannot be listed, read, or removed.
        """
        with self._lock:
            self._drafts_dir.mkdir(parents=True, exist_ok=True)
            removed = 0
            for directory in self._drafts_dir.iterdir():
                if (
                    not _UUID.fullmatch(directory.name)
                    or not directory.is_dir()
                ):
                    continue
                try:
                    draft = self.read_draft(directory.name)
                except AppError as error:
                    if error.status_code == 404:
                        continue
                    raise
                if now_ms - draft["createdAt"] > _EXPIRY_MS:
                    self.remove_draft(directory.name)
                    removed += 1
            return removed

    def remove_draft(self, draft_id: str) -> None:
        """Remove a validated draft directory, tolerating a missing draft.

        Args:
            draft_id: UUID of the draft directory to remove.

        Raises:
            AppError: The UUID or draft path is invalid (400).
            OSError: An existing draft directory cannot be removed.
        """
        with self._lock:
            directory = self._draft_path(draft_id)
            if directory.is_symlink():
                raise AppError("草稿路径无效")
            if directory.exists():
                shutil.rmtree(directory)
