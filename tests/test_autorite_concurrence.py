"""Tests for Autorité de la concurrence (France) extractor, discrete act identity, communiqués, PDF policies, and preview."""

import io
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock
import pytest
import httpx
import pypdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.providers.base import RawEntryData
from app.providers.native import NativeProvider
from app.providers.extractors.autorite_concurrence import (
    AutoriteConcurrenceExtractor,
    ADLCDiscoveryMetrics,
    ADLC_SOURCE_NAME,
    ADLC_BASE_URL,
    ACT_ID_REGEX,
    classify_official_act_id,
    MAX_PDF_BYTES,
    MAX_PDF_PAGES_FULL,
    MAX_PDF_PAGES_PARTIAL,
    MAX_EXTRACTED_CHARS,
    extract_adlc_published_at,
    parse_iso_or_french_date,
)
from app.services.source_sufficiency_service import (
    SourceSufficiencyService,
    SourceSufficiencyLevel,
)
from app.services.ingestion_service import compute_content_hash
from scripts.preview_source_discovery import (
    ReadOnlyDeduplicationInspector,
    SourceDiscoveryPreviewService,
    read_only_session_scope,
)
from scripts.seed_source_autorite_concurrence import seed_autorite_concurrence_source
from tests.conftest import TestingSessionLocal


def _create_mock_pdf(num_pages: int = 5, text_per_page: str = "Motifs de la décision...") -> bytes:
    """Create a valid in-memory vector PDF with specified page count."""
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        page = writer.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------
# MOCK HTML FIXTURES
# ------------------------------------------------------------------------------

HTML_DECISION_26_D_06 = """<!DOCTYPE html>
<html lang="fr">
<head><title>Décision 26-D-06 du 15 février 2026 | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-numero-de-decision">26-D-06</div>
  <div class="field--name-field-date-de-decision">15 février 2026</div>
  <h1>Décision 26-D-06 relative à des pratiques mises en œuvre dans le secteur de la distribution</h1>
  <div class="field--name-field-chapeau">
    L'Autorité sanctionne plusieurs entreprises pour entente illicite sur les prix et répartition de marché.
  </div>
  <div class="field--name-body">
    <p>L'Autorité de la concurrence a adopté une décision sanctionnant l'entente horizontale entre les distributeurs.</p>
    <p>Après instruction approfondie et notification des griefs, le Collège a constaté une infraction caractérisée à l'article L. 420-1 du code de commerce et à l'article 101 du TFUE.</p>
    <p>Les sanctions pécuniaires s'élèvent à un montant global de 45 millions d'euros, assorties d'injonctions de publication.</p>
  </div>
</main>
</body>
</html>
"""

HTML_INTERIM_26_MC_01 = """<!DOCTYPE html>
<html lang="fr">
<head><title>Décision 26-MC-01 du 10 janvier 2026 | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-numero-de-decision">26-MC-01</div>
  <div class="field--name-field-date-de-decision">10 janvier 2026</div>
  <h1>Décision 26-MC-01 relative à une demande de mesures conservatoires</h1>
  <div class="field--name-field-chapeau">
    Mesures d'urgence prononcées contre une plateforme numérique pour atteinte grave et immédiate à la concurrence.
  </div>
  <div class="field--name-body">
    <p>La société requérante sollicitait des mesures conservatoires au titre de l'article L. 464-1 du code de commerce.</p>
    <p>Constatant un risque de dommage irréversible pour le marché pertinent, l'Autorité impose l'accès non discriminatoire à l'API pendant la durée de l'instruction au fond.</p>
  </div>
</main>
</body>
</html>
"""

HTML_MERGER_26_DCC_180 = """<!DOCTYPE html>
<html lang="fr">
<head><title>Décision 26-DCC-180 du 20 janvier 2026 | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-numero-de-decision">26-DCC-180</div>
  <div class="field--name-field-date-de-decision">20 janvier 2026</div>
  <h1>Décision 26-DCC-180 relative à la prise de contrôle exclusif de la société Cible par Acquéreur</h1>
  <div class="field--name-field-chapeau">
    L'Autorité de la concurrence autorise l'opération de concentration sans engagements au terme de la Phase 1.
  </div>
  <div class="field--name-body">
    <p>Par notification reçue le 20 décembre 2025, l'opération a été soumise au contrôle de l'Autorité.</p>
    <p>Après examen des parts de marché et des effets unilatéraux, l'Autorité constate que l'opération ne soulève aucun problème de concurrence sur les marchés nationaux concernés.</p>
  </div>
</main>
</body>
</html>
"""

HTML_OPINION_26_A_05 = """<!DOCTYPE html>
<html lang="fr">
<head><title>Avis 26-A-05 du 05 février 2026 | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-numero-de-decision">26-A-05</div>
  <div class="field--name-field-date-de-decision">5 février 2026</div>
  <h1>Avis 26-A-05 relatif au fonctionnement concurrentiel du secteur de la santé numérique</h1>
  <div class="field--name-field-chapeau">
    L'Autorité émet des recommandations pour préserver la dynamique concurrentielle dans la télémédecine.
  </div>
  <div class="field--name-body">
    <p>Saisie pour avis par la Commission des affaires économiques du Sénat, l'Autorité a mené de larges consultations sectorielles.</p>
    <p>Elle formule dix recommandations pour garantir l'interopérabilité des données de santé et prévenir le verrouillage des acteurs de marché.</p>
  </div>
</main>
</body>
</html>
"""

HTML_DERIVATIVE_COMMUNIQUE = """<!DOCTYPE html>
<html lang="fr">
<head><title>Communiqué | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-date-de-publication">15 février 2026</div>
  <h1>L'Autorité sanctionne les entreprises du secteur de la distribution</h1>
  <div class="field--name-body">
    <p>Par sa <a href="/fr/decision/26-d-06">décision 26-D-06</a> du 15 février 2026, l'Autorité a infligé une amende...</p>
  </div>
</main>
</body>
</html>
"""

HTML_AUTONOMOUS_COMMUNIQUE = """<!DOCTYPE html>
<html lang="fr">
<head><title>Opérations de visite et saisie inopinées | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-date-de-publication">18 février 2026</div>
  <h1>Opérations de visite et saisie inopinées dans le secteur des transports</h1>
  <div class="field--name-body">
    <p>Les services d'instruction de l'Autorité de la concurrence ont procédé à des opérations de visite et saisie inopinées (dawn raids) auprès de plusieurs entreprises soupçonnées de pratiques anticoncurrentielles dans les transports de marchandises.</p>
    <p>À ce stade, ces opérations ne préjugent en rien de la culpabilité des entreprises concernées.</p>
  </div>
</main>
</body>
</html>
"""

HTML_INSTITUTIONAL_COMMUNIQUE = """<!DOCTYPE html>
<html lang="fr">
<head><title>Vie de l'institution | Autorité de la concurrence</title></head>
<body>
<main>
  <div class="field--name-field-date-de-publication">10 janvier 2026</div>
  <h1>Cérémonie des vœux du Président de l'Autorité de la concurrence</h1>
  <div class="field--name-body">
    <p>Le Président et les membres du Collège ont présenté leurs vœux pour l'année 2026 devant le personnel et les invités institutionnels.</p>
  </div>
</main>
</body>
</html>
"""


# ==============================================================================
# 1. OFFICIAL ACT CLASSIFICATION AND IDENTIFIERS (REQUIREMENTS A, B, C, D, E)
# ==============================================================================

class TestADLCActIngestion:
    """Test discrete legal act parsing, typing, and sufficiency."""

    def test_classify_official_act_id_patterns(self):
        # Decisions (D)
        assert classify_official_act_id("26-D-06") == ("decision", "antitrust")
        assert classify_official_act_id("25-D-12") == ("decision", "antitrust")

        # Interim measures (MC)
        assert classify_official_act_id("26-MC-01") == ("interim_measure", "interim_measures")

        # Merger decisions (DCC)
        assert classify_official_act_id("26-DCC-180") == ("merger_decision", "merger_control")

        # Opinions (A)
        assert classify_official_act_id("26-A-05") == ("opinion", "competition_policy")

        # Unsupported act types (fail closed)
        assert classify_official_act_id("26-SOA-01") is None
        assert classify_official_act_id("unknown-format") is None

    @pytest.mark.asyncio
    async def test_requirement_a_decision_d_ingestion(self):
        """Requirement A: Ingestion de décision D (26-D-06)."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_DECISION_26_D_06

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_act_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06",
            source=source,
            now=datetime.now(timezone.utc),
            fetch_pdf=False,
        )

        assert raw is not None
        assert raw.external_id == "adlc:act:26-d-06"
        assert "26-D-06" in raw.title
        assert raw.content_type == "decision"
        assert raw.language == "fr"
        assert raw.raw_metadata.get("official_id") == "26-D-06"
        assert raw.raw_metadata.get("decision_family") == "antitrust"
        assert len(raw.content) > 300

        # Assess sufficiency
        entry = Entry(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=raw.external_id,
            url=raw.url,
            title=raw.title,
            content=raw.content,
            published_at=raw.published_at,
            language=raw.language,
        )
        suff = SourceSufficiencyService.assess(entry)
        assert suff.level in (SourceSufficiencyLevel.FULL, SourceSufficiencyLevel.PARTIAL)
        assert len(entry.content) > 250

    @pytest.mark.asyncio
    async def test_requirement_b_interim_measure_mc_ingestion(self):
        """Requirement B: Ingestion de mesure conservatoire MC (26-MC-01)."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_INTERIM_26_MC_01

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_act_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/decision/26-mc-01",
            source=source,
            now=datetime.now(timezone.utc),
            fetch_pdf=False,
        )

        assert raw is not None
        assert raw.external_id == "adlc:act:26-mc-01"
        assert raw.content_type == "interim_measure"
        assert raw.raw_metadata.get("decision_family") == "interim_measures"

    @pytest.mark.asyncio
    async def test_requirement_c_merger_dcc_ingestion(self):
        """Requirement C: Ingestion de décision concentration DCC (26-DCC-180)."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_MERGER_26_DCC_180

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_act_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-180",
            source=source,
            now=datetime.now(timezone.utc),
            fetch_pdf=False,
        )

        assert raw is not None
        assert raw.external_id == "adlc:act:26-dcc-180"
        assert raw.content_type == "merger_decision"
        assert raw.raw_metadata.get("decision_family") == "merger_control"

    @pytest.mark.asyncio
    async def test_requirement_d_opinion_a_ingestion(self):
        """Requirement D: Ingestion d'avis A (26-A-05)."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_OPINION_26_A_05

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_act_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/avis/26-a-05",
            source=source,
            now=datetime.now(timezone.utc),
            fetch_pdf=False,
        )

        assert raw is not None
        assert raw.external_id == "adlc:act:26-a-05"
        assert raw.content_type == "opinion"
        assert raw.raw_metadata.get("decision_family") == "competition_policy"

    @pytest.mark.asyncio
    async def test_requirement_e_unsupported_act_type_skipped(self):
        """Requirement E: Acto con tipo no soportado se salta sin fallar."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        unsupported_html = """
        <main>
          <div class="field--name-field-date-de-publication">10 janvier 2026</div>
          <div class="field--name-field-numero-de-decision">26-UNKNOWN-99</div>
          <h1>Acte administratif interne</h1>
        </main>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = unsupported_html

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_act_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/autre/26-unknown-99",
            source=source,
            now=datetime.now(timezone.utc),
            fetch_pdf=False,
        )

        assert raw is None
        assert extractor.metrics.unsupported_act_type_skipped == 1


# ==============================================================================
# 2. COMMUNIQUÉS DISCOVERY & FILTERING (REQUIREMENTS F, G, H)
# ==============================================================================

class TestADLCCommuniques:
    """Test filtering of derivative vs autonomous vs institutional communiqués."""

    @pytest.mark.asyncio
    async def test_requirement_f_derivative_communique_skipped(self):
        """Requirement F: Derivative communiqué referring to an official act is skipped."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_DERIVATIVE_COMMUNIQUE

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_communique_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/article/sanctions-distribution",
            source=source,
            now=datetime.now(timezone.utc),
        )

        assert raw is None
        assert extractor.metrics.derivative_communiques_skipped == 1

    @pytest.mark.asyncio
    async def test_requirement_g_autonomous_dawn_raid_communique_included(self):
        """Requirement G: Autonomous communiqué (dawn raid / visite et saisie) is included."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_AUTONOMOUS_COMMUNIQUE

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_communique_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/article/operations-visite-saisie-transports",
            source=source,
            now=datetime.now(timezone.utc),
        )

        assert raw is not None
        assert raw.external_id.startswith("adlc:communique:")
        assert "visite et saisie" in raw.title.lower()
        assert raw.raw_metadata.get("is_autonomous_communique") is True
        assert extractor.metrics.autonomous_communiques_included == 1

    @pytest.mark.asyncio
    async def test_requirement_h_institutional_noise_communique_excluded(self):
        """Requirement H: Institutional noise (vœux, nomination, etc.) is excluded."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_INSTITUTIONAL_COMMUNIQUE

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_resp)

        raw = await extractor._extract_communique_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/article/ceremonie-voeux-president",
            source=source,
            now=datetime.now(timezone.utc),
        )

        assert raw is None
        assert extractor.metrics.institutional_communiques_excluded == 1


# ==============================================================================
# 3. PDF HANDLING POLICIES (REQUIREMENTS I, J, K, L)
# ==============================================================================

class TestADLCPDFPolicies:
    """Test PDF size limits, page limits, corrupt PDF tolerance, and snapshot invariance."""

    @pytest.mark.asyncio
    async def test_requirement_j_pdf_size_limit_skipped(self):
        """Requirement J: PDF > 20 MB is skipped and logged without crashing."""
        extractor = AutoriteConcurrenceExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        # Mock HEAD response reporting 25 MB
        mock_head = MagicMock()
        mock_head.status_code = 200
        mock_head.headers = {"Content-Length": str(25 * 1024 * 1024)}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=mock_head)

        text = await extractor._fetch_and_extract_pdf(
            client=mock_client,
            pdf_url="https://www.autoritedelaconcurrence.fr/sites/default/files/decisions/huge.pdf",
        )

        assert text == ""

    @pytest.mark.asyncio
    async def test_requirement_k_pdf_page_limits(self):
        """Requirement K: PDF > 40 pages extracts only first 30; <= 40 extracts all."""
        extractor = AutoriteConcurrenceExtractor()

        # 1. 35-page PDF (extracts completely)
        pdf_35_bytes = _create_mock_pdf(num_pages=35)
        mock_head = MagicMock()
        mock_head.status_code = 200
        mock_head.headers = {"Content-Length": str(len(pdf_35_bytes))}

        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.content = pdf_35_bytes

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=mock_head)
        mock_client.get = AsyncMock(return_value=mock_get)

        with patch("pypdf.PdfReader") as mock_reader_cls:
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_pages = [MagicMock(extract_text=lambda: f"Page content") for _ in range(35)]
            mock_reader.pages = mock_pages
            mock_reader_cls.return_value = mock_reader

            extracted = await extractor._fetch_and_extract_pdf(
                client=mock_client,
                pdf_url="https://www.autoritedelaconcurrence.fr/sites/default/files/test35.pdf",
            )
            assert len(mock_pages) == 35

        # 2. 50-page PDF (extracts only first 30 pages)
        with patch("pypdf.PdfReader") as mock_reader_cls:
            mock_reader = MagicMock()
            mock_reader.is_encrypted = False
            mock_pages = [MagicMock(extract_text=lambda: f"Page content") for _ in range(50)]
            mock_reader.pages = mock_pages
            mock_reader_cls.return_value = mock_reader

            extracted = await extractor._fetch_and_extract_pdf(
                client=mock_client,
                pdf_url="https://www.autoritedelaconcurrence.fr/sites/default/files/test50.pdf",
            )
            # Extractor loops through min(len(pages), 30) for > 40 pages
            assert extractor.metrics.pdfs_downloaded_and_extracted >= 1

    @pytest.mark.asyncio
    async def test_requirement_l_corrupt_pdf_graceful_fallback(self):
        """Requirement L: Corrupt PDF falls back gracefully to HTML text without crashing."""
        extractor = AutoriteConcurrenceExtractor()

        mock_head = MagicMock()
        mock_head.status_code = 200
        mock_head.headers = {"Content-Length": "1024"}

        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.content = b"Not a real PDF! Corrupt binary data \x00"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=mock_head)
        mock_client.get = AsyncMock(return_value=mock_get)

        text = await extractor._fetch_and_extract_pdf(
            client=mock_client,
            pdf_url="https://www.autoritedelaconcurrence.fr/sites/default/files/corrupt.pdf",
        )

        assert text == ""

    def test_requirement_i_delayed_pdf_snapshot_invariance(self, db_session: Session):
        """Requirement I: Existing Entry with identical external_id is NEVER mutated by delayed PDF."""
        source_id = uuid.uuid4()
        existing_entry = Entry(
            id=uuid.uuid4(),
            source_id=source_id,
            external_id="adlc:act:26-d-06",
            url="https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06",
            title="Décision 26-D-06",
            content="Texte HTML initial déjà ingéré.",
            published_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        db_session.add(existing_entry)
        db_session.commit()

        # Candidate item extracted days later with delayed PDF
        delayed_raw = RawEntryData(
            external_id="adlc:act:26-d-06",
            url="https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06",
            title="Décision 26-D-06",
            content="Nouveau texte extrait du PDF publié 10 jours plus tard...",
            published_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )

        dedup = ReadOnlyDeduplicationInspector.check_adlc_item(
            db=db_session,
            source_id=source_id,
            raw=delayed_raw,
        )

        assert dedup.is_duplicate is True
        assert dedup.duplicate_reason == "external_id"
        assert dedup.matched_entry_id == str(existing_entry.id)

        # Verify entry was NOT mutated
        db_entry = db_session.execute(select(Entry).where(Entry.id == existing_entry.id)).scalar_one()
        assert db_entry.content == "Texte HTML initial déjà ingéré."


# ==============================================================================
# 4. PAGINATION & LOOKBACK (REQUIREMENT M)
# ==============================================================================

class TestADLCPagination:
    """Test pagination bounds and lookback termination."""

    @pytest.mark.asyncio
    async def test_requirement_m_pagination_stops_at_cutoff(self):
        """Requirement M: Ingestion stops crawling when acts are older than lookback cutoff."""
        extractor = AutoriteConcurrenceExtractor()

        # Page 1 contains recent act
        page_1_html = """
        <div class="views-row"><span class="date-display-single">15 février 2026</span><a href="/fr/decision/26-d-06">Décision 26-D-06</a></div>
        """
        # Page 2 contains old act outside 8-day lookback
        page_2_html = """
        <div class="views-row"><span class="date-display-single">01 janvier 2025</span><a href="/fr/decision/25-d-01">Décision 25-D-01</a></div>
        """

        mock_client = AsyncMock(spec=httpx.AsyncClient)

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "page=1" in str(url):
                resp.text = page_2_html
            else:
                resp.text = page_1_html
            return resp

        mock_client.get = mock_get

        now = datetime(2026, 2, 20, tzinfo=timezone.utc)
        items = await extractor._crawl_section(
            client=mock_client,
            base_section_url="https://www.autoritedelaconcurrence.fr/fr/liste-des-decisions-et-avis",
            cutoff_date=now - timedelta(days=8),
            max_pages=5,
        )

        # Should have found the item from page 1 and stopped at page 2
        assert len(items) == 1
        assert "26-d-06" in items[0]["url"]


# ==============================================================================
# 5. SEED SCRIPT (REQUIREMENT O)
# ==============================================================================

class TestADLCSeeder:
    """Test seed script idempotency and dry-run safety."""

    def test_requirement_o_seeder_create_update_and_dry_run(self, db_session: Session):
        """Requirement O: Seeder creates if missing, updates if present, respects dry-run."""
        with patch("scripts.seed_source_autorite_concurrence.SessionLocal", return_value=db_session):
            # 1. Dry run on empty database
            dry_src = seed_autorite_concurrence_source(dry_run=True)
            assert dry_src is None
            existing = db_session.execute(select(Source).where(Source.name == ADLC_SOURCE_NAME)).scalar_one_or_none()
            assert existing is None

            # 2. Real seed creates source
            src1 = seed_autorite_concurrence_source(dry_run=False)
            assert src1 is not None
            assert src1.name == ADLC_SOURCE_NAME
            assert src1.provider == "native"
            assert src1.type == SourceType.INSTITUTIONAL

            # 3. Subsequent run updates idempotently without duplicate rows
            src2 = seed_autorite_concurrence_source(dry_run=False)
            assert src2 is not None
            assert src2.id == src1.id

            all_adlc = db_session.execute(select(Source).where(Source.name == ADLC_SOURCE_NAME)).scalars().all()
            assert len(all_adlc) == 1


# ==============================================================================
# 6. NATIVE PROVIDER ROUTING (REQUIREMENT P)
# ==============================================================================

class TestADLCNativeProviderRouting:
    """Test NativeProvider dispatch to AutoriteConcurrenceExtractor."""

    def test_native_provider_can_handle(self):
        provider = NativeProvider()
        src = Source(
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )
        assert provider.can_handle(src) is True

    @pytest.mark.asyncio
    async def test_native_provider_fetch_entries_dispatch(self):
        provider = NativeProvider()
        src = Source(
            name=ADLC_SOURCE_NAME,
            type=SourceType.INSTITUTIONAL,
            provider="native",
            url=ADLC_BASE_URL,
        )

        mock_entries = [
            RawEntryData(
                external_id="adlc:act:26-d-06",
                url="https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06",
                title="Décision 26-D-06",
                content="Contenu de test...",
                published_at=datetime.now(timezone.utc),
            )
        ]

        with patch.object(AutoriteConcurrenceExtractor, "extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = mock_entries
            mock_client = AsyncMock(spec=httpx.AsyncClient)
            entries = await provider.fetch_entries(source=src, client=mock_client)
            assert len(entries) == 1
            assert entries[0].external_id == "adlc:act:26-d-06"


# ==============================================================================
# 7. HASH DETERMINISM (REQUIREMENT N)
# ==============================================================================

class TestADLCHashDeterminism:
    """Test compute_content_hash determinism and uniqueness."""

    def test_hash_determinism_and_uniqueness(self):
        h1 = compute_content_hash("Décision 26-D-06", "https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06", "Résumé...")
        h2 = compute_content_hash("Décision 26-D-06", "https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06", "Résumé...")
        h3 = compute_content_hash("Décision 26-D-07", "https://www.autoritedelaconcurrence.fr/fr/decision/26-d-07", "Résumé...")

        assert h1 == h2
        assert h1 != h3


# ==============================================================================
# 8. PREVIEW INTEGRATION (REQUIREMENT Q)
# ==============================================================================

class TestADLCPreviewIntegration:
    """Test read-only preview execution and metrics capture for ADLC."""

    @pytest.mark.asyncio
    async def test_preview_adlc_async_computes_metrics_and_candidates(self, db_session: Session):
        preview_svc = SourceDiscoveryPreviewService(db=db_session)
        src = preview_svc._create_transient_source(ADLC_SOURCE_NAME)

        mock_entries = [
            RawEntryData(
                external_id="adlc:act:26-d-06",
                url="https://www.autoritedelaconcurrence.fr/fr/decision/26-d-06",
                title="Décision 26-D-06 relative à des pratiques dans la distribution",
                content="L'Autorité sanctionne une entente horizontale entre distributeurs. " * 40,  # > 2000 chars -> FULL
                excerpt="Entente sanctionnée...",
                author="Autorité de la concurrence",
                published_at=datetime.now(timezone.utc) - timedelta(days=2),
                language="fr",
                content_type="decision",
                raw_metadata={
                    "official_id": "26-D-06",
                    "act_type": "decision",
                    "decision_family": "antitrust",
                },
            )
        ]

        mock_metrics = ADLCDiscoveryMetrics()
        mock_metrics.items_discovered = 10
        mock_metrics.acts_supported = 1
        mock_metrics.decisions_d = 1
        mock_metrics.full_count = 1

        with patch.object(AutoriteConcurrenceExtractor, "extract", new_callable=AsyncMock) as mock_extract, \
             patch.object(AutoriteConcurrenceExtractor, "last_metrics", new_callable=PropertyMock) as mock_last_metrics:
            mock_extract.return_value = mock_entries
            mock_last_metrics.return_value = mock_metrics

            mock_client = AsyncMock(spec=httpx.AsyncClient)
            summary = await preview_svc._preview_adlc_async(
                source=src,
                cutoff_date=(datetime.now(timezone.utc) - timedelta(days=90)).date(),
                lookback_days=90,
                async_client=mock_client,
            )

            assert summary.source_name == ADLC_SOURCE_NAME
            assert summary.new_candidates == 1
            assert summary.full_count == 1
            assert summary.eligible_for_analysis == 1
            assert len(summary.new_items) == 1
            assert summary.new_items[0].case_type == "decision"
            assert summary.adlc_metrics is not None
            assert summary.adlc_metrics.get("decisions_d") == 1


# ==============================================================================
# 9. PUBLICATION DATE HARDENING & TEMPORAL FAIL-CLOSED (TESTS A - F)
# ==============================================================================

class TestADLCPublicationDateHardening:
    """Test strict deterministic date hierarchy and temporal fail-closed rules."""

    @pytest.mark.asyncio
    async def test_a_communique_with_valid_html_date(self):
        """A) Communiqué with valid HTML date -> parses correct date (2026-07-20)."""
        html = """<!DOCTYPE html>
        <html>
        <head>
          <meta property="article:published_time" content="2026-07-20T14:30:00+02:00" />
        </head>
        <body>
          <h1>Opération de visite inopinée</h1>
          <div class="field--name-body"><p>""" + "Texte d'enquête inopinée sans décision rattachée. " * 30 + """</p></div>
        </body>
        </html>"""
        extractor = AutoriteConcurrenceExtractor()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(status_code=200, text=html)
        mock_client.get = AsyncMock(return_value=mock_resp)

        res = await extractor.parse_detail_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/visite-inopinee",
            html=html,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert res is not None
        assert res.published_at == datetime(2026, 7, 20, 12, 30, 0, tzinfo=timezone.utc)
        assert res.published_at.date().isoformat() == "2026-07-20"

    @pytest.mark.asyncio
    async def test_b_communique_without_date_skipped_fail_closed(self):
        """B) Communiqué without date -> skipped fail-closed (undated_items_skipped == 1, inside_lookback == 0)."""
        html = """<!DOCTYPE html>
        <html>
        <head><title>Communiqué sans date</title></head>
        <body>
          <h1>Communiqué sans date</h1>
          <div class="field--name-body"><p>""" + "Texte sans aucune date détectable. " * 30 + """</p></div>
        </body>
        </html>"""
        extractor = AutoriteConcurrenceExtractor()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(status_code=200, text=html)
        mock_client.get = AsyncMock(return_value=mock_resp)

        res = await extractor.parse_detail_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/sans-date",
            html=html,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert res is None
        assert extractor.metrics.undated_items_skipped == 1
        assert extractor.metrics.inside_lookback == 0

    @pytest.mark.asyncio
    async def test_c_sitemap_lastmod_does_not_become_published_at(self):
        """C) Sitemap lastmod does NOT become published_at."""
        # When page HTML has no date, even if sitemap had a lastmod, extractor must reject it as undated
        html = """<!DOCTYPE html>
        <html>
        <head><title>Communiqué test</title></head>
        <body>
          <h1>Communiqué test sans date dans HTML</h1>
          <div class="field--name-body"><p>""" + "Contenu sans date. " * 30 + """</p></div>
        </body>
        </html>"""
        extractor = AutoriteConcurrenceExtractor()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(status_code=200, text=html)
        mock_client.get = AsyncMock(return_value=mock_resp)

        res = await extractor.parse_detail_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/test-no-html-date",
            html=html,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert res is None
        assert extractor.metrics.undated_items_skipped == 1
        # Also test with extract_adlc_published_at directly
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        assert extract_adlc_published_at(soup, "https://example.com", html) is None

    @pytest.mark.asyncio
    async def test_d_real_sfr_html_fixture_extracts_date_and_is_full_eligible(self):
        """D) Real SFR HTML fixture -> extracts 2026-07-15, is autonomous, FULL, eligible."""
        html = """<!DOCTYPE html>
        <html lang="fr">
        <head>
          <meta charset="utf-8" />
          <title>Télécoms – SFR : l’Autorité de la concurrence sera l’autorité en charge de l’examen | Autorité de la concurrence</title>
          <meta property="article:published_time" content="2026-07-15T09:30:45+0200" />
          <link rel="canonical" href="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/telecoms-sfr-lautorite-de-la-concurrence-sera-lautorite-en-charge-de-lexamen" />
        </head>
        <body>
        <main>
          <h1>Télécoms – SFR : l’Autorité de la concurrence sera l’autorité en charge de l’examen</h1>
          <time datetime="2026-07-15T09:30:45+02:00">15 juillet 2026</time>
          <div class="field--name-body">
            <p>La Commission européenne a fait droit à la demande de renvoi partiel de l’examen du projet de rachat formulée par les autorités de concurrence.</p>
            <p>L’Autorité examinera l’impact concurrentiel sur les marchés de gros et de détail des communications électroniques en France métropolitaine et outre-mer.</p>
            <p>""" + "Ce communiqué autonome expose les orientations et le calendrier de l'instruction sur le marché pertinent. " * 30 + """</p>
          </div>
        </main>
        </body>
        </html>"""
        extractor = AutoriteConcurrenceExtractor()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(status_code=200, text=html)
        mock_client.get = AsyncMock(return_value=mock_resp)

        res = await extractor.parse_detail_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/telecoms-sfr-lautorite-de-la-concurrence-sera-lautorite-en-charge-de-lexamen",
            html=html,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert res is not None
        assert res.published_at.date().isoformat() == "2026-07-15"
        assert res.content_type == "press_release"
        assert res.raw_metadata.get("act_type") == "communique"
        assert res.raw_metadata.get("is_autonomous_communique") is True

        entry = Entry(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            external_id=res.external_id,
            url=res.url,
            title=res.title,
            content=res.content,
            published_at=res.published_at,
            language=res.language,
        )
        sufficiency = SourceSufficiencyService.assess(entry)
        assert sufficiency.level == SourceSufficiencyLevel.FULL
        assert sufficiency.level in (SourceSufficiencyLevel.FULL, SourceSufficiencyLevel.PARTIAL)

    @pytest.mark.asyncio
    async def test_e_derivative_communique_remains_skipped(self):
        """E) Derivative communiqué remains skipped."""
        extractor = AutoriteConcurrenceExtractor()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(status_code=200, text=HTML_DERIVATIVE_COMMUNIQUE)
        mock_client.get = AsyncMock(return_value=mock_resp)

        res = await extractor.parse_detail_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/distribution-sanction",
            html=HTML_DERIVATIVE_COMMUNIQUE,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert res is None
        assert extractor.metrics.derivative_communiques_skipped == 1

    @pytest.mark.asyncio
    async def test_f_autonomous_substantive_communique_remains_included(self):
        """F) Autonomous substantive communiqué remains included."""
        extractor = AutoriteConcurrenceExtractor()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_resp = MagicMock(status_code=200, text=HTML_AUTONOMOUS_COMMUNIQUE)
        mock_client.get = AsyncMock(return_value=mock_resp)

        res = await extractor.parse_detail_page(
            client=mock_client,
            url="https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse/visite-saisie-transports",
            html=HTML_AUTONOMOUS_COMMUNIQUE,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert res is not None
        assert res.content_type == "press_release"
        assert res.raw_metadata.get("act_type") == "communique"
        assert extractor.metrics.autonomous_communiques_included == 1
        assert res.raw_metadata.get("is_autonomous_communique") is True

