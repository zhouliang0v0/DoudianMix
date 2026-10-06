"""Deliver private job images and build the legacy ZIP download."""

import io
import json
import pathlib
import zipfile

from backend import errors


def read_job_image(job_dir: pathlib.Path, folder: str, name: str) -> bytes:
    """Read a plain metadata filename confined to the requested job folder.

    Args:
        job_dir: Validated storage directory for this job.
        folder: Internal originals or results directory.
        name: Filename associated with a stored asset or module record.

    Returns:
        Unchanged image bytes.

    Raises:
        AppError: Filename, path, or file is unavailable (404).
        OSError: A present file cannot be read.
    """
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or any(character in name for character in "/\\:")
    ):
        raise errors.AppError("图片不存在", 404)
    directory = job_dir / folder
    path = directory / name
    if (
        job_dir.is_symlink()
        or directory.is_symlink()
        or path.is_symlink()
        or not path.resolve().is_relative_to(job_dir.resolve())
    ):
        raise errors.AppError("图片不存在", 404)
    try:
        return path.read_bytes()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise errors.AppError("图片不存在", 404) from error


def build_job_archive(job: dict, job_dir: pathlib.Path) -> bytes:
    """Build the Node-compatible manifest, copy list and completed images.

    Args:
        job: Persisted camelCase job dictionary without process credentials.
        job_dir: Validated job directory containing the results folder.

    Returns:
        A deflated ZIP as bytes. Missing or unsafe result files are omitted.
    """
    output = io.BytesIO()
    manifest_modules = []
    copy = []
    images = []
    for order, module in enumerate(job["modules"], 1):
        filename = module.get("imageFile")
        image_name = None
        if filename:
            try:
                content = read_job_image(job_dir, "results", filename)
                image_name = f"images/{order:02d}-{filename}"
                if module["status"] == "completed":
                    images.append((image_name, content))
            except errors.AppError:
                pass
        manifest_modules.append(
            {
                "order": order,
                **{
                    key: module.get(key)
                    for key in ("name", "status", "headline", "body", "error")
                },
                "image": image_name,
            }
        )
        failure = (
            f"失败原因：{module['error']}\n" if module.get("error") else ""
        )
        copy.append(
            f"{order}. {module.get('name', '')}\n状态：{module['status']}\n"
            f"标题：{module.get('headline') or '待补充'}\n"
            f"文案：{module.get('body') or '待补充'}\n{failure}"
        )
    manifest = {
        "id": job["id"],
        "settings": job.get("settings"),
        "analysis": job.get("analysis"),
        "modules": manifest_modules,
    }
    with zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
        archive.writestr("文案与失败清单.txt", "\n".join(copy))
        for name, content in images:
            archive.writestr(name, content)
    return output.getvalue()
