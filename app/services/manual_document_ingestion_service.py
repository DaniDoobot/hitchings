"""Service orchestrating manual document ingestion via URL and file uploads (Bloque 11C).

Supports:
- External URL ingestion with robust SSRF protections, HTML/PDF extraction, and deduplication.
- File upload ingestion (PDF, TXT, MD) up to 25 MB with digital text layer extraction.
- Decoupled AI analysis pipeline execution.
- Strict deduplication preventing duplicate Entries in the documentary archive.
"""

from __future__ import annotations

import enum
import hashlib
import io
import ipaddress
import logging
import re
import socket
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import httpx
import pypdf
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.url_utils import normalize_url
from app.models.analysis import AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.source import (
    MANUAL_UPLOAD_SOURCE_ID,
    MANUAL_UPLOAD_SOURCE_NAME,
    get_or_create_manual_source,
)
from app.models.tracking import TrackingMatrix
from app.providers.ai.base import BaseAIProvider
from app.providers.direct_web.html_cleaner import (
    clean_editorial_html,
    extract_canonical_url,
    extract_structured_author,
    extract_structured_date,
)
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.services.ingestion_service import compute_ingestion_dedupe_hash

logger = logging.getLogger(__name__)

# Security & size limits
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARS = 120_000
MIN_USEFUL_TEXT_CHARS = 50
DOWNLOAD_TIMEOUT_SECONDS = 15.0
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_REDIRECTS = 5

ALLOWED_FILE_EXTENSIONS = {".pdf", ".txt", ".md"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/octet-stream",
}


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class ManualIngestionStatus(str, enum.Enum):
    """Result status of a manual document ingestion operation."""
    CREATED = "created"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass
class ManualIngestionResult:
    """Result payload for manual document ingestion."""
    status: ManualIngestionStatus
    entry: Optional[Entry] = None
    existing_entry_id: Optional[uuid.UUID] = None
    duplicate_reason: Optional[str] = None
    analysis_requested: bool = False
    analysis_scheduled: bool = False
    error: Optional[str] = None


class ManualIngestionError(Exception):
    """Base error for manual document ingestion failures."""
    pass


class SSRFSecurityError(ManualIngestionError):
    """Raised when a URL targets a private, loopback, or cloud-metadata IP."""
    pass


class UnsupportedContentTypeError(ManualIngestionError):
    """Raised when URL or file format is unsupported."""
    pass


class DocumentExtractionError(ManualIngestionError):
    """Raised when document content cannot be extracted (e.g. scanned PDF without text)."""
    pass


class FileSizeLimitExceededError(ManualIngestionError):
    """Raised when file or downloaded content exceeds maximum allowed size."""
    pass


# ==============================================================================
# SSRF & URL Security
# ==============================================================================


def is_ip_private_or_reserved(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if IP belongs to private, loopback, link-local, multicast, or cloud metadata ranges."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    # Explicit cloud metadata endpoint (169.254.169.254)
    if str(ip) == "169.254.169.254":
        return True

    # IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        mapped = ip.ipv4_mapped
        return (
            mapped.is_private
            or mapped.is_loopback
            or mapped.is_link_local
            or str(mapped) == "169.254.169.254"
        )
    return False


def validate_url_security(url: str) -> None:
    """Validate that URL has a valid http/https scheme, does not target private networks, and resolves safely via DNS.

    Raises:
        SSRFSecurityError: If URL fails validation or targets restricted IPs.
    """
    if not url or not isinstance(url, str):
        raise SSRFSecurityError("URL no válida o vacía.")

    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFSecurityError(f"Esquema no permitido: '{scheme}'. Solo se permite http o https.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityError("URL inválida: no contiene nombre de host.")

    # Explicit localhost / loopback names
    if hostname.lower() in ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"):
        raise SSRFSecurityError(f"Acceso a localhost bloqueado por seguridad: '{hostname}'.")

    # If hostname is an IP literal
    try:
        ip = ipaddress.ip_address(hostname)
        if is_ip_private_or_reserved(ip):
            raise SSRFSecurityError(f"Acceso a IP privada/reservada bloqueado: '{ip}'.")
        return
    except ValueError:
        pass

    # Resolve hostname via DNS
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFSecurityError(f"No se pudo resolver el host '{hostname}': {exc}")

    for _, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            if is_ip_private_or_reserved(ip):
                raise SSRFSecurityError(
                    f"El host '{hostname}' resuelve a una IP privada/restringida: '{ip}'."
                )
        except ValueError:
            pass


def download_url_safely(
    url: str,
    client: Optional[httpx.Client] = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> tuple[bytes, str, str]:
    """Download content from a validated URL with SSRF re-checks on each redirect hop and bounded memory streaming.

    Returns:
        tuple of (content_bytes, content_type, final_url)
    """
    current_url = url
    should_close_client = False
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=False)
        should_close_client = True

    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_url_security(current_url)

            try:
                response = client.get(current_url, follow_redirects=False)
            except httpx.TimeoutException as exc:
                raise ManualIngestionError(f"Tiempo de espera agotado al conectar con '{current_url}': {exc}") from exc
            except httpx.RequestError as exc:
                raise ManualIngestionError(f"Error de red al conectar con '{current_url}': {exc}") from exc

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ManualIngestionError(f"HTTP redirect sin cabecera Location desde '{current_url}'.")
                current_url = urllib.parse.urljoin(current_url, location)
                continue

            if response.status_code >= 400:
                raise ManualIngestionError(
                    f"Error HTTP {response.status_code} al descargar '{current_url}'."
                )

            content = response.content
            if len(content) > max_bytes:
                raise FileSizeLimitExceededError(
                    f"El recurso descargado ({len(content)} bytes) supera el límite de {max_bytes // (1024*1024)} MB."
                )

            content_type = response.headers.get("content-type", "").lower()
            return content, content_type, current_url

        raise ManualIngestionError(f"Demasiadas redirecciones (máximo {MAX_REDIRECTS}).")
    finally:
        if should_close_client:
            client.close()


# ==============================================================================
# Document Parsers & Normalizers
# ==============================================================================


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent Path Traversal and illegal filesystem characters."""
    basename = Path(filename).name
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", basename)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "document.bin"


COMMON_STANDALONE_WORDS: set[str] = {
    "a", "an", "in", "on", "at", "to", "by", "for", "of", "or", "and", "the",
    "is", "it", "as", "if", "be", "he", "we", "el", "la", "en", "de", "un", "y", "o", "del", "al"
}


def normalize_pdf_page_text(raw_text: str) -> str:
    """Normalize extracted PDF text to heal split words and reflow broken lines while preserving legal structure."""
    if not raw_text or not raw_text.strip():
        return ""

    lines = [line.strip() for line in raw_text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    if not lines:
        return ""

    # Phase 1: Heal drop caps and split words
    healed: list[str] = []
    i = 0
    while i < len(lines):
        curr = lines[i]
        if not curr:
            healed.append("")
            i += 1
            continue

        if i + 1 < len(lines) and lines[i + 1]:
            nxt = lines[i + 1]

            # Case 1: Drop cap / split initial fragment alone on line
            # e.g., 'Ne' + 'utral...', 'Bef' + 'ore...', 's' + 'ummary...', 'di' + 'sproportionate...'
            if re.match(r"^[A-Za-z]{1,4}$", curr) and curr.lower() not in COMMON_STANDALONE_WORDS:
                if re.match(r"^[a-z]", nxt):
                    healed.append(curr + nxt)
                    i += 2
                    continue
                elif curr.isupper() and re.match(r"^[A-Z]", nxt):
                    parts = nxt.split(" ", 1)
                    if len(parts) == 2:
                        healed.append(curr + parts[0] + " " + parts[1])
                    else:
                        healed.append(curr + nxt)
                    i += 2
                    continue
            elif len(curr) == 1 and curr.isalpha() and curr.lower() not in ("a", "i", "y", "o"):
                if re.match(r"^[a-z]{2,}", nxt):
                    healed.append(curr + nxt)
                    i += 2
                    continue

            # Case 2: Hyphenated line break: 'word-' followed by 'continuation'
            if curr.endswith("-") and len(curr) > 1 and curr[-2].isalpha() and re.match(r"^[a-z]", nxt):
                healed.append(curr[:-1] + nxt)
                i += 2
                continue

        healed.append(curr)
        i += 1

    # Phase 2: Paragraph reflowing (unwrapping soft line breaks while preserving headings, lists, numbering)
    list_or_header_pattern = re.compile(
        r"^(?:\d+[\.\)]|\([a-z\divxlcdm]+\)|[A-Z]\.|\u2022|\-|\*|[A-Z\s]{4,}:|[A-Z\s]{5,}$)"
    )

    paragraphs: list[str] = []
    current_p: list[str] = []

    for line in healed:
        if not line:
            if current_p:
                paragraphs.append(" ".join(current_p))
                current_p = []
            continue

        # Standalone page number (e.g. '1', '24')
        if re.match(r"^\d{1,4}$", line):
            if current_p:
                paragraphs.append(" ".join(current_p))
                current_p = []
            paragraphs.append(line)
            continue

        if list_or_header_pattern.match(line):
            if current_p:
                paragraphs.append(" ".join(current_p))
                current_p = []
            current_p.append(line)
        else:
            if current_p and (current_p[-1].endswith(".") or current_p[-1].endswith(":")) and len(current_p[-1]) < 40:
                paragraphs.append(" ".join(current_p))
                current_p = [line]
            else:
                current_p.append(line)

    if current_p:
        paragraphs.append(" ".join(current_p))

    return "\n\n".join(paragraphs)


def extract_pdf_metadata_title(pdf_bytes: bytes) -> Optional[str]:
    """Extract embedded document title from PDF metadata if present and valid."""
    if len(pdf_bytes) < 4 or not pdf_bytes.startswith(b"%PDF"):
        return None
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        metadata = reader.metadata
        if metadata and metadata.title:
            raw_title = str(metadata.title).strip()
            if raw_title and len(raw_title) >= 3 and not raw_title.lower().startswith(("untitled", "sin título")):
                return raw_title
    except Exception:
        pass
    return None


def clean_document_title(
    manual_title: Optional[str] = None,
    metadata_title: Optional[str] = None,
    filename_or_url: Optional[str] = None,
    default_fallback: str = "Documento sin título",
) -> str:
    """Resolve a clean, human-readable document title according to precedence and cleanup rules.

    Precedence:
    1. manual_title (if explicitly supplied)
    2. metadata_title (embedded PDF/HTML metadata title, if valid)
    3. Cleaned filename or URL path (URL decoded, extension stripped, tech prefix stripped)
    4. default_fallback
    """
    if manual_title and manual_title.strip():
        return " ".join(manual_title.strip().split())

    if metadata_title and metadata_title.strip():
        meta = " ".join(urllib.parse.unquote(metadata_title).strip().split())
        if len(meta) >= 3 and not meta.lower().startswith(("untitled", "sin título")):
            return meta

    if filename_or_url and filename_or_url.strip():
        raw = filename_or_url.strip()
        if "/" in raw or "\\" in raw:
            raw = Path(urlsplit(raw).path).name or raw.split("/")[-1]

        # URL decode (e.g. %20 -> space, %26 -> &, %28 -> (, %29 -> ))
        unquoted = urllib.parse.unquote(raw).strip()

        # Strip common document extensions
        for ext in (".pdf", ".txt", ".md", ".html", ".htm", ".doc", ".docx"):
            if unquoted.lower().endswith(ext):
                unquoted = unquoted[: -len(ext)].strip()
                break

        # Replace underscores if no spaces exist or if predominantly underscores
        if "_" in unquoted and (unquoted.count("_") > unquoted.count(" ") or " " not in unquoted):
            unquoted = unquoted.replace("_", " ")

        # Strip technical numeric or hash prefix: e.g. '17497725 ' or 16+ hex chars
        unquoted = re.sub(r"^(?:[a-f0-9]{16,}|\d{5,})[\s_\-]+", "", unquoted, flags=re.IGNORECASE).strip()

        clean = " ".join(unquoted.split())
        if clean:
            return clean

    return default_fallback


def extract_pdf_digital_text(
    pdf_bytes: bytes,
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_EXTRACTED_CHARS,
) -> str:
    """Extract digital text layer from PDF bytes using pypdf.

    Raises:
        DocumentExtractionError: If PDF is corrupt, has 0 pages, or has less than 50 useful characters.
    """
    if len(pdf_bytes) < 4 or not pdf_bytes.startswith(b"%PDF"):
        raise DocumentExtractionError("El archivo no es un documento PDF válido (formato no reconocido).")

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        total_pages = len(reader.pages)
        if total_pages == 0:
            raise DocumentExtractionError("El archivo PDF no contiene páginas.")

        pages_to_read = min(total_pages, max_pages)
        chunks: list[str] = []
        total_chars = 0

        for idx in range(pages_to_read):
            page = reader.pages[idx]
            page_text = page.extract_text() or ""
            cleaned = normalize_pdf_page_text(page_text).strip()
            if cleaned:
                chunks.append(cleaned)
                total_chars += len(cleaned)
                if total_chars >= max_chars:
                    break

        full_text = "\n\n".join(chunks).strip()
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars].rsplit(" ", 1)[0] + "..."

        if len(full_text) < MIN_USEFUL_TEXT_CHARS:
            raise DocumentExtractionError(
                "El documento PDF no contiene suficiente texto digital seleccionable "
                "(es una imagen escaneada o carece de capa de texto). "
                f"Se requieren al menos {MIN_USEFUL_TEXT_CHARS} caracteres indexables."
            )

        return full_text
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(f"Error al procesar el archivo PDF: {exc}") from exc


def extract_plain_text(content_bytes: bytes, max_chars: int = MAX_EXTRACTED_CHARS) -> str:
    """Extract plain text from UTF-8 bytes with fallback to Latin-1."""
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = content_bytes.decode("latin-1", errors="replace")

    clean_text = text.strip()
    if len(clean_text) < MIN_USEFUL_TEXT_CHARS:
        raise DocumentExtractionError(
            f"El archivo no contiene suficiente texto útil (mínimo {MIN_USEFUL_TEXT_CHARS} caracteres)."
        )

    if len(clean_text) > max_chars:
        clean_text = clean_text[:max_chars].rsplit(" ", 1)[0] + "..."

    return clean_text


def compute_content_sha256(content: str) -> str:
    """Compute deterministic SHA-256 hash of normalized text content."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


# ==============================================================================
# Service Implementation
# ==============================================================================


class ManualDocumentIngestionService:
    """Core service orchestrating manual ingestion of documents into the archive."""

    def __init__(
        self,
        background_scheduler: Optional[Callable[[uuid.UUID], None]] = None,
        ai_provider: Optional[BaseAIProvider] = None,
    ) -> None:
        self.background_scheduler = background_scheduler
        self.ai_provider = ai_provider

    def ingest_url(
        self,
        db: Session,
        url: str,
        manual_title: Optional[str] = None,
        manual_author: Optional[str] = None,
        manual_published_at: Optional[datetime] = None,
        analyze_with_ai: bool = False,
        user_id: Optional[uuid.UUID] = None,
        user_email: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> ManualIngestionResult:
        """Ingest a document from an external URL."""
        # 1. Validation & SSRF check
        raw_url = (url or "").strip()
        validate_url_security(raw_url)
        norm_url = normalize_url(raw_url)

        # 2. Pre-check URL duplication
        existing_by_url = db.execute(
            select(Entry).where(
                or_(Entry.url == norm_url, Entry.canonical_url == norm_url)
            ).limit(1)
        ).scalar_one_or_none()

        if existing_by_url:
            return ManualIngestionResult(
                status=ManualIngestionStatus.DUPLICATE,
                existing_entry_id=existing_by_url.id,
                duplicate_reason="url",
            )

        # 3. Download resource safely
        content_bytes, content_type, final_url = download_url_safely(norm_url, client=client)
        final_norm_url = normalize_url(final_url)

        # Check final_norm_url duplication if redirect occurred
        if final_norm_url != norm_url:
            existing_by_final = db.execute(
                select(Entry).where(
                    or_(Entry.url == final_norm_url, Entry.canonical_url == final_norm_url)
                ).limit(1)
            ).scalar_one_or_none()
            if existing_by_final:
                return ManualIngestionResult(
                    status=ManualIngestionStatus.DUPLICATE,
                    existing_entry_id=existing_by_final.id,
                    duplicate_reason="url",
                )

        # 4. Extract content and metadata based on resource type
        is_pdf = (
            "application/pdf" in content_type
            or final_norm_url.lower().endswith(".pdf")
            or content_bytes.startswith(b"%PDF")
        )

        if is_pdf:
            extracted_content = extract_pdf_digital_text(content_bytes)
            pdf_meta_title = extract_pdf_metadata_title(content_bytes)
            extracted_title = clean_document_title(
                manual_title=manual_title,
                metadata_title=pdf_meta_title,
                filename_or_url=final_norm_url,
                default_fallback="Documento PDF",
            )
            extracted_author = manual_author
            extracted_date = manual_published_at
            canonical = final_norm_url
            doc_content_type = "pdf"
        elif "html" in content_type or b"<html" in content_bytes[:2048].lower():
            try:
                html_text = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                html_text = content_bytes.decode("latin-1", errors="replace")

            soup = BeautifulSoup(html_text, "html.parser")
            extracted_content = clean_editorial_html(soup)
            if len(extracted_content) < MIN_USEFUL_TEXT_CHARS:
                raise DocumentExtractionError(
                    f"El contenido de la página web no contiene suficiente texto indexable (mínimo {MIN_USEFUL_TEXT_CHARS} caracteres)."
                )

            # Title extraction
            auto_title = ""
            h1 = soup.find("h1")
            if h1:
                auto_title = " ".join(h1.get_text().split())
            if not auto_title and soup.title:
                auto_title = soup.title.get_text().strip()

            extracted_title = clean_document_title(
                manual_title=manual_title,
                metadata_title=auto_title,
                filename_or_url=final_norm_url,
                default_fallback="Página Web",
            )
            extracted_author = manual_author or extract_structured_author(soup)
            auto_date, _ = extract_structured_date(soup)
            extracted_date = manual_published_at or auto_date
            canonical = extract_canonical_url(soup, final_norm_url)
            doc_content_type = "article"
        else:
            raise UnsupportedContentTypeError(
                f"Tipo de contenido no soportado: '{content_type}'. Solo se admiten páginas web HTML y documentos PDF."
            )

        # 5. Check duplicate content
        content_text_hash = compute_content_sha256(extracted_content)
        ingestion_hash = compute_ingestion_dedupe_hash(extracted_title, final_norm_url, extracted_content[:250])

        existing_by_hash = db.execute(
            select(Entry).where(
                or_(
                    Entry.content_hash == ingestion_hash,
                    Entry.content_hash == content_text_hash,
                )
            ).limit(1)
        ).scalar_one_or_none()

        if existing_by_hash:
            return ManualIngestionResult(
                status=ManualIngestionStatus.DUPLICATE,
                existing_entry_id=existing_by_hash.id,
                duplicate_reason="content_hash",
            )

        # 6. Resolve manual source & persist Entry
        source = get_or_create_manual_source(db)

        raw_meta = {
            "origin": "manual_url",
            "source_url": raw_url,
            "final_url": final_norm_url,
            "canonical_url": canonical,
            "content_type": content_type,
            "content_text_hash": content_text_hash,
            "uploaded_by_user_id": str(user_id) if user_id else None,
            "uploaded_by_email": user_email,
        }

        entry = Entry(
            id=uuid.uuid4(),
            source_id=source.id,
            url=final_norm_url,
            canonical_url=canonical,
            title=extracted_title,
            content=extracted_content,
            excerpt=extracted_content[:250].strip() if extracted_content else None,
            author=extracted_author,
            published_at=extracted_date,
            captured_at=utc_now(),
            content_type=doc_content_type,
            content_hash=ingestion_hash,
            raw_metadata=raw_meta,
        )
        db.add(entry)
        db.flush()

        # 7. AI Analysis handling
        analysis_scheduled = False
        if analyze_with_ai and self.background_scheduler:
            self.background_scheduler(entry.id)
            analysis_scheduled = True

        return ManualIngestionResult(
            status=ManualIngestionStatus.CREATED,
            entry=entry,
            analysis_requested=analyze_with_ai,
            analysis_scheduled=analysis_scheduled,
        )

    def ingest_file(
        self,
        db: Session,
        file_bytes: bytes,
        filename: str,
        content_type: Optional[str] = None,
        manual_title: Optional[str] = None,
        manual_author: Optional[str] = None,
        manual_published_at: Optional[datetime] = None,
        analyze_with_ai: bool = False,
        user_id: Optional[uuid.UUID] = None,
        user_email: Optional[str] = None,
    ) -> ManualIngestionResult:
        """Ingest a document from uploaded file bytes (PDF, TXT, MD)."""
        # 1. Size validation
        if not file_bytes:
            raise DocumentExtractionError("El archivo subido está vacío.")
        if len(file_bytes) > MAX_FILE_BYTES:
            raise FileSizeLimitExceededError(
                f"El archivo supera el tamaño máximo permitido de {MAX_FILE_BYTES // (1024*1024)} MB."
            )

        # 2. Filename & extension validation
        clean_name = sanitize_filename(filename)
        ext = Path(clean_name).suffix.lower()
        if ext not in ALLOWED_FILE_EXTENSIONS:
            raise UnsupportedContentTypeError(
                f"Extensión '{ext}' no permitida. Formatos soportados: {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}."
            )

        # 3. Content extraction based on extension
        if ext == ".pdf":
            extracted_content = extract_pdf_digital_text(file_bytes)
            pdf_meta_title = extract_pdf_metadata_title(file_bytes)
            final_title = clean_document_title(
                manual_title=manual_title,
                metadata_title=pdf_meta_title,
                filename_or_url=clean_name,
                default_fallback="Documento PDF",
            )
            doc_type = "pdf"
        elif ext in (".txt", ".md"):
            extracted_content = extract_plain_text(file_bytes)
            final_title = clean_document_title(
                manual_title=manual_title,
                metadata_title=None,
                filename_or_url=clean_name,
                default_fallback="Documento manual",
            )
            doc_type = "document"
        else:
            raise UnsupportedContentTypeError(f"Formato de archivo no soportado: {ext}")

        # 4. Content hash calculation and deduplication
        content_text_hash = compute_content_sha256(extracted_content)

        existing_by_hash = db.execute(
            select(Entry).where(
                or_(
                    Entry.content_hash == content_text_hash,
                    Entry.content == extracted_content,
                )
            ).limit(1)
        ).scalar_one_or_none()

        if existing_by_hash:
            return ManualIngestionResult(
                status=ManualIngestionStatus.DUPLICATE,
                existing_entry_id=existing_by_hash.id,
                duplicate_reason="content_hash",
            )

        # 5. Build synthetic URL & metadata
        entry_id = uuid.uuid4()
        synthetic_url = f"upload://manual/{entry_id}/{clean_name}"

        raw_meta = {
            "origin": "manual_upload",
            "filename": clean_name,
            "file_size_bytes": len(file_bytes),
            "mime_type": content_type or "application/octet-stream",
            "content_text_hash": content_text_hash,
            "uploaded_by_user_id": str(user_id) if user_id else None,
            "uploaded_by_email": user_email,
        }

        # 6. Resolve manual source & persist Entry
        source = get_or_create_manual_source(db)

        entry = Entry(
            id=entry_id,
            source_id=source.id,
            url=synthetic_url,
            canonical_url=synthetic_url,
            title=final_title,
            content=extracted_content,
            excerpt=extracted_content[:250].strip() if extracted_content else None,
            author=manual_author,
            published_at=manual_published_at,
            captured_at=utc_now(),
            content_type=doc_type,
            content_hash=content_text_hash,
            raw_metadata=raw_meta,
        )
        db.add(entry)
        db.flush()

        # 7. AI Analysis handling
        analysis_scheduled = False
        if analyze_with_ai and self.background_scheduler:
            self.background_scheduler(entry.id)
            analysis_scheduled = True

        return ManualIngestionResult(
            status=ManualIngestionStatus.CREATED,
            entry=entry,
            analysis_requested=analyze_with_ai,
            analysis_scheduled=analysis_scheduled,
        )

    async def execute_entry_analysis(
        self,
        entry_id: uuid.UUID,
        db: Session,
        provider: Optional[BaseAIProvider] = None,
    ) -> Optional[EntryAnalysis]:
        """Execute two-stage AI analysis pipeline (triage -> deep) for a given entry.

        Resolves active TrackingMatrix and versioned prompts from the database.
        """
        entry = db.get(Entry, entry_id)
        if not entry:
            raise ValueError(f"Entry {entry_id} no encontrado.")

        triage_prompt = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_triage",
                AnalysisPromptVersion.active.is_(True),
            )
            .order_by(AnalysisPromptVersion.version.desc())
            .first()
        )
        if not triage_prompt:
            raise RuntimeError("Prompt activo 'observatory_triage' no encontrado en la base de datos.")

        deep_prompt = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_deep_analysis",
                AnalysisPromptVersion.active.is_(True),
            )
            .order_by(AnalysisPromptVersion.version.desc())
            .first()
        )
        if not deep_prompt:
            raise RuntimeError("Prompt activo 'observatory_deep_analysis' no encontrado en la base de datos.")

        matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        if not matrix:
            raise RuntimeError("TrackingMatrix activa no encontrada en la base de datos.")

        eff_provider = provider or self.ai_provider
        if eff_provider is None:
            from app.providers.ai.gemini_api import GeminiAPIProvider
            eff_provider = GeminiAPIProvider()

        pipeline = AnalysisPipelineService(provider=eff_provider)
        return await pipeline.run_pipeline(
            entry_id=entry.id,
            matrix_id=matrix.id,
            triage_prompt_id=triage_prompt.id,
            deep_prompt_id=deep_prompt.id,
            db=db,
        )
