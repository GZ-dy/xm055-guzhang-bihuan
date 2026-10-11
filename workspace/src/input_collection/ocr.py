from __future__ import annotations

from pathlib import Path
from typing import Any


def extract_screenshot_texts(paths: list[Path]) -> list[dict[str, Any]]:
    return [_extract_one(path) for path in paths]


def _extract_one(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    result: dict[str, Any] = {
        "path": str(resolved),
        "name": resolved.name,
        "exists": resolved.exists(),
        "text": "",
        "source": "",
        "status": "missing_file" if not resolved.exists() else "unavailable",
        "blocked_reason": "",
    }
    if not resolved.exists():
        result["blocked_reason"] = "screenshot file does not exist"
        return result

    sidecar = _sidecar_text_path(resolved)
    if sidecar:
        result.update(
            {
                "text": sidecar.read_text(encoding="utf-8-sig").strip(),
                "source": "sidecar",
                "sidecar_path": str(sidecar),
                "status": "ok",
            }
        )
        return result

    text, error = _pytesseract_text(resolved)
    if text:
        result.update({"text": text.strip(), "source": "pytesseract", "status": "ok"})
    else:
        result["blocked_reason"] = error or "no OCR backend available; add a .ocr.txt sidecar"
    return result


def _sidecar_text_path(path: Path) -> Path | None:
    for candidate in (path.with_suffix(".ocr.txt"), path.with_suffix(".txt"), Path(str(path) + ".txt")):
        if candidate.exists():
            return candidate
    return None


def _pytesseract_text(path: Path) -> tuple[str, str]:
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except ImportError as exc:
        return "", f"optional OCR dependency missing: {exc.name}"
    try:
        image = Image.open(path)
        image = ImageOps.autocontrast(ImageOps.grayscale(image))
        if max(image.size) < 1800:
            image = image.resize((image.width * 2, image.height * 2))
        return pytesseract.image_to_string(image, lang="chi_sim+eng", config="--psm 6"), ""
    except Exception as exc:  # pragma: no cover - depends on local OCR runtime.
        return "", f"OCR failed: {type(exc).__name__}: {exc}"
