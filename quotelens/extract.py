"""Get text out of a quotation: PDF text layer first, OCR for scans and photos."""
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
import pytesseract

PDF_MIN_CHARS = 40
IMAGE_EXT = {".png", ".jpg", ".jpeg"}


class ExtractionError(Exception):
    pass


def _skew_angle(binary: np.ndarray) -> float:
    """Angle (degrees) that maximises row-profile variance, i.e. makes text lines horizontal."""
    h, w = binary.shape
    scale = 600 / max(h, w)
    small = cv2.resize(binary, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else binary
    sh, sw = small.shape
    best, best_score = 0.0, -1.0
    for angle in np.arange(-4.0, 4.01, 0.25):
        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), angle, 1.0)
        rot = cv2.warpAffine(small, m, (sw, sh), flags=cv2.INTER_NEAREST)
        score = float(np.var(rot.sum(axis=1)))
        if score > best_score:
            best, best_score = float(angle), score
    return best


def preprocess(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    gray = cv2.medianBlur(gray, 3)
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    angle = _skew_angle(ink)
    if abs(angle) > 0.1:
        h, w = gray.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        gray = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return _remove_grid(gray)


def _remove_grid(gray: np.ndarray) -> np.ndarray:
    """Binarise and erase long horizontal/vertical rules (table borders), which confuse OCR."""
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = ink.shape
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 20), 1)))
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 40, 20))))
    lines = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    cleaned = cv2.bitwise_and(ink, cv2.bitwise_not(lines))
    return cv2.bitwise_not(cleaned)


UPSCALE = 1.5   # measured: 1.0 -> 22/51 items recovered exactly, 1.5 -> 37/51 on one test set (see docs/evaluation.md)


def ocr_image(img_bgr: np.ndarray) -> str:
    img_bgr = cv2.resize(img_bgr, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
    try:
        return pytesseract.image_to_string(preprocess(img_bgr), config="--psm 6")
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractionError("OCR engine (tesseract) is not installed") from exc


def extract(path) -> tuple:
    """Returns (kind, text). kind is 'pdf_text', 'pdf_scan' or 'image'."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            with pdfplumber.open(path) as pdf:
                if not pdf.pages:
                    raise ExtractionError("PDF has no pages")
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
                if len(text.strip()) >= PDF_MIN_CHARS:
                    return "pdf_text", text
                pages = [cv2.cvtColor(np.array(p.to_image(resolution=300).original.convert("RGB")), cv2.COLOR_RGB2BGR)
                         for p in pdf.pages[:10]]
        except ExtractionError:
            raise
        except Exception as exc:  # corrupt or encrypted PDF
            raise ExtractionError(f"could not read PDF: {exc}") from exc
        return "pdf_scan", "\n".join(ocr_image(p) for p in pages)
    if ext in IMAGE_EXT:
        img = cv2.imread(str(path))
        if img is None:
            raise ExtractionError("could not decode image")
        return "image", ocr_image(img)
    raise ExtractionError(f"unsupported file type {ext!r}")
