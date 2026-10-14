import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DocumentsPage } from '../pages/DocumentsPage';
import { DocumentDetailPage } from '../pages/DocumentDetailPage';
import { AddDocumentModal } from '../components/documents/AddDocumentModal';
import { ExportDossierModal } from '../components/documents/ExportDossierModal';
import {
  documentsApi,
  ApiError,
  parseContentDispositionFilename,
  triggerBrowserDownload,
  mapDossierExportErrorMessage,
} from '../services/documentsApi';
import { observatoryApi } from '../services/observatoryApi';
import { formatDocumentTitle, renderDocumentContent, healHistoricalContent } from '../utils/documentFormatter';
import { DocumentListItem } from '../types/document';

describe('Documentary Archive Module (Bloque 11B Frontend)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders DocumentsPage listing documents with analysis status badges', async () => {
    vi.spyOn(observatoryApi, 'getSources').mockResolvedValueOnce([
      { id: 'src-1', name: 'Tribunal Supremo', type: 'website', entry_count: 5 },
    ]);

    vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
      items: [
        {
          id: 'doc-1',
          title: 'Sentencia Cártel de Camiones',
          url: 'https://example.com/doc-1',
          source: { id: 'src-1', name: 'Tribunal Supremo' },
          captured_at: '2026-09-01T10:00:00Z',
          published_at: '2026-09-01T10:00:00Z',
          is_linkedin: false,
          source_origin_category: 'institutional',
          has_analysis: true,
          analysis: {
            id: 'ana-1',
            status: 'completed',
            relevance_status: 'relevant',
            relevance_score: 95,
            summary: 'Resumen jurídico de la sentencia',
            key_points: ['Punto 1'],
            canonical_topics: [{ code: 'antitrust', name: 'Antitrust' }],
          },
        },
        {
          id: 'doc-2',
          title: 'Nota informativa preliminar',
          url: 'https://example.com/doc-2',
          source: { id: 'src-1', name: 'Tribunal Supremo' },
          captured_at: '2026-09-02T10:00:00Z',
          is_linkedin: false,
          source_origin_category: 'institutional',
          has_analysis: false,
          analysis: null,
          excerpt: 'Extracto breve preliminar...',
        },
      ],
      total: 2,
      limit: 20,
      offset: 0,
    });

    render(
      <MemoryRouter initialEntries={['/documentos']}>
        <Routes>
          <Route path="/documentos" element={<DocumentsPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Sentencia Cártel de Camiones')).toBeInTheDocument();
      expect(screen.getByText('Nota informativa preliminar')).toBeInTheDocument();
      expect(screen.getByText('Analizado por IA')).toBeInTheDocument();
      expect(screen.getByText('Archivo original')).toBeInTheDocument();
    });
  });

  it('renders DocumentDetailPage showing full text and distinction between source and AI analysis', async () => {
    vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce({
      id: 'doc-1',
      title: 'Sentencia Cártel de Camiones',
      url: 'https://example.com/doc-1',
      source: { id: 'src-1', name: 'Tribunal Supremo', tracked_entity_name: 'Sala de lo Civil' },
      captured_at: '2026-09-01T10:00:00Z',
      published_at: '2026-09-01T10:00:00Z',
      is_linkedin: false,
      source_origin_category: 'institutional',
      content: 'Texto íntegro de los fundamentos jurídicos del fallo.',
      has_analysis: true,
      analysis: {
        id: 'ana-1',
        status: 'completed',
        relevance_status: 'relevant',
        relevance_score: 95,
        summary: 'Síntesis técnica de la responsabilidad civil ex delicto.',
        key_points: ['Infracción constatada'],
        canonical_topics: [{ code: 'antitrust', name: 'Antitrust' }],
      },
      evidence: {
        source: 'deep',
        summary_quotes: [{ source_field: 'content', quote: 'fundamentos jurídicos del fallo' }],
        key_points: [],
      },
    });

    render(
      <MemoryRouter initialEntries={['/documentos/doc-1']}>
        <Routes>
          <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Sentencia Cártel de Camiones')).toBeInTheDocument();
      expect(screen.getByText('Cuerpo Documental Original')).toBeInTheDocument();
      expect(screen.getByText('Texto íntegro de los fundamentos jurídicos del fallo.')).toBeInTheDocument();
      expect(screen.getByText('Dictamen Jurídico IA')).toBeInTheDocument();
      expect(screen.getByText('Síntesis técnica de la responsabilidad civil ex delicto.')).toBeInTheDocument();
      expect(screen.getByText('"fundamentos jurídicos del fallo"')).toBeInTheDocument();
    });
  });

  it('renders DocumentDetailPage gracefully when entry has no analysis', async () => {
    vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce({
      id: 'doc-2',
      title: 'Actualización sin análisis',
      url: 'https://example.com/doc-2',
      source: { id: 'src-1', name: 'Tribunal Supremo' },
      captured_at: '2026-09-02T10:00:00Z',
      is_linkedin: false,
      source_origin_category: 'institutional',
      content: 'Contenido original sin análisis.',
      has_analysis: false,
      analysis: null,
      evidence: null,
    });

    render(
      <MemoryRouter initialEntries={['/documentos/doc-2']}>
        <Routes>
          <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Actualización sin análisis')).toBeInTheDocument();
      expect(screen.getByText(/Sin análisis jurídico disponible/i)).toBeInTheDocument();
      expect(screen.getByText('Contenido original sin análisis.')).toBeInTheDocument();
    });
  });
});

describe('Manual Document Ingestion (Bloque 11C Frontend)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('1. El botón "Añadir documento" aparece en DocumentsPage', async () => {
    vi.spyOn(observatoryApi, 'getSources').mockResolvedValueOnce([]);
    vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 });

    render(
      <MemoryRouter initialEntries={['/documentos']}>
        <DocumentsPage />
      </MemoryRouter>
    );

    expect(screen.getByRole('button', { name: /Añadir documento/i })).toBeInTheDocument();
  });

  it('2. El modal se abre al hacer clic en "Añadir documento"', async () => {
    vi.spyOn(observatoryApi, 'getSources').mockResolvedValueOnce([]);
    vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 });

    render(
      <MemoryRouter initialEntries={['/documentos']}>
        <DocumentsPage />
      </MemoryRouter>
    );

    const btn = screen.getByRole('button', { name: /Añadir documento/i });
    fireEvent.click(btn);

    expect(screen.getByRole('heading', { name: /Añadir Documento al Fondo/i })).toBeInTheDocument();
  });

  it('3. Se puede cambiar entre URL y archivo', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    // Default mode is URL
    expect(screen.getByLabelText(/URL del documento/i)).toBeInTheDocument();

    // Switch to File mode
    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));
    expect(screen.getByText(/Haga clic para seleccionar o arrastre un archivo aquí/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/URL del documento/i)).not.toBeInTheDocument();

    // Switch back to URL mode
    fireEvent.click(screen.getByRole('button', { name: /Desde URL/i }));
    expect(screen.getByLabelText(/URL del documento/i)).toBeInTheDocument();
  });

  it('4. URL válida habilita el envío', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    const submitBtn = screen.getByRole('button', { name: /^Añadir documento$/i });
    expect(submitBtn).toBeDisabled();

    const input = screen.getByLabelText(/URL del documento/i);
    fireEvent.change(input, { target: { value: 'https://cnmc.es/expediente/101.pdf' } });

    expect(submitBtn).not.toBeDisabled();
  });

  it('5. URL inválida muestra validación al enviar', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    const input = screen.getByLabelText(/URL del documento/i);
    fireEvent.change(input, { target: { value: 'ftp://invalido.org' } });

    const submitBtn = screen.getByRole('button', { name: /^Añadir documento$/i });
    fireEvent.click(submitBtn);

    expect(screen.getByText(/La URL debe comenzar por http:\/\/ o https:\/\//i)).toBeInTheDocument();
  });

  it('6. Upload PDF: selecciona archivo PDF correctamente', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));

    const file = new File(['%PDF-1.4 dummy'], 'resolucion.pdf', { type: 'application/pdf' });
    const input = document.getElementById('file-upload-input') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText('resolucion.pdf')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Añadir documento$/i })).not.toBeDisabled();
  });

  it('7. Upload TXT: selecciona archivo TXT correctamente', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));

    const file = new File(['Dictamen de competencia'], 'dictamen.txt', { type: 'text/plain' });
    const input = document.getElementById('file-upload-input') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText('dictamen.txt')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Añadir documento$/i })).not.toBeDisabled();
  });

  it('8. Upload MD: selecciona archivo MD correctamente', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));

    const file = new File(['# Informe'], 'informe.md', { type: 'text/markdown' });
    const input = document.getElementById('file-upload-input') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText('informe.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Añadir documento$/i })).not.toBeDisabled();
  });

  it('9. Archivo no soportado muestra error de validación', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));

    const file = new File(['malware'], 'script.exe', { type: 'application/x-msdownload' });
    const input = document.getElementById('file-upload-input') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText(/Formato de archivo no soportado/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Añadir documento$/i })).toBeDisabled();
  });

  it('10. Archivo >25 MB muestra error de tamaño máximo', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));

    // Mock an oversized file of 26 MB
    const hugeFile = new File(['x'], 'huge.pdf', { type: 'application/pdf' });
    Object.defineProperty(hugeFile, 'size', { value: 26 * 1024 * 1024 });

    const input = document.getElementById('file-upload-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [hugeFile] } });

    expect(screen.getByText(/El archivo supera el tamaño máximo permitido de 25 MB/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Añadir documento$/i })).toBeDisabled();
  });

  it('11. Opción analyze_with_ai puede ser desmarcada o marcada', () => {
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeChecked();

    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
  });

  it('12. Llamada correcta a ingestDocumentFromUrl con parámetros esperados', async () => {
    const spy = vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockResolvedValueOnce({
      status: 'created',
      entry_id: 'doc-url-123',
      title: 'Resolución CNMC',
      url: 'https://cnmc.es/exp/123.pdf',
      analysis_requested: true,
      analysis_scheduled: true,
      created_at: new Date().toISOString(),
    });

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://cnmc.es/exp/123.pdf' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith({
        url: 'https://cnmc.es/exp/123.pdf',
        analyze_with_ai: true,
        manual_title: undefined,
        manual_author: undefined,
        manual_published_at: undefined,
      });
    });
  });

  it('13. Llamada correcta a uploadDocument con parámetros esperados', async () => {
    const spy = vi.spyOn(documentsApi, 'uploadDocument').mockResolvedValueOnce({
      status: 'created',
      entry_id: 'doc-upload-456',
      title: 'Dictamen',
      url: 'upload://manual/uuid/dictamen.pdf',
      analysis_requested: false,
      analysis_scheduled: false,
      created_at: new Date().toISOString(),
    });

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));

    // Uncheck analyze_with_ai
    fireEvent.click(screen.getByRole('checkbox'));

    const file = new File(['pdf text'], 'dictamen.pdf', { type: 'application/pdf' });
    const input = document.getElementById('file-upload-input') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({
          file,
          analyze_with_ai: false,
        })
      );
    });
  });

  it('14. Estado de loading muestra "Procesando..." y deshabilita botones', async () => {
    let resolvePromise!: (val: any) => void;
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolvePromise = resolve;
        })
    );

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://cnmc.es/doc.pdf' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    expect(screen.getByText(/Procesando.../i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Procesando.../i })).toBeDisabled();

    // Resolve promise to clean up
    resolvePromise({
      status: 'created',
      entry_id: 'doc-done',
      url: 'https://cnmc.es/doc.pdf',
      analysis_requested: true,
      analysis_scheduled: true,
      created_at: new Date().toISOString(),
    });
  });

  it('15. Éxito: muestra confirmación y programación de IA', async () => {
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockResolvedValueOnce({
      status: 'created',
      entry_id: 'doc-success-1',
      title: 'Sentencia TJUE',
      url: 'https://curia.europa.eu/judgment.pdf',
      analysis_requested: true,
      analysis_scheduled: true,
      created_at: new Date().toISOString(),
    });

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://curia.europa.eu/judgment.pdf' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Documento añadido correctamente/i)).toBeInTheDocument();
      expect(screen.getByText(/El análisis IA ha quedado programado y se ejecutará en segundo plano/i)).toBeInTheDocument();
      expect(screen.getByText('Ver documento')).toBeInTheDocument();
    });
  });

  it('16. Error 401 muestra mensaje comprensible de sesión expirada', async () => {
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockRejectedValueOnce(
      new ApiError('No autenticado', 401)
    );

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://example.com/doc.pdf' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Sesión no válida o expirada/i)).toBeInTheDocument();
    });
  });

  it('17. Error 409 muestra aviso de documento duplicado', async () => {
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockResolvedValueOnce({
      status: 'duplicate',
      existing_entry_id: 'doc-existente-uuid',
      duplicate_reason: 'url',
      message: 'El documento ya existe en el fondo documental (duplicado por url).',
    });

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://example.com/duplicate.pdf' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Este documento ya existe en el Fondo Documental/i)).toBeInTheDocument();
      expect(screen.getByText(/Ver documento existente/i)).toBeInTheDocument();
    });
  });

  it('18. Error 413 muestra mensaje de archivo demasiado grande', async () => {
    vi.spyOn(documentsApi, 'uploadDocument').mockRejectedValueOnce(
      new ApiError('Too large', 413)
    );

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));
    const file = new File(['pdf text'], 'test.pdf', { type: 'application/pdf' });
    fireEvent.change(document.getElementById('file-upload-input')!, { target: { files: [file] } });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/El archivo supera el tamaño máximo permitido de 25 MB/i)).toBeInTheDocument();
    });
  });

  it('19. Error 415 muestra mensaje de formato no soportado', async () => {
    vi.spyOn(documentsApi, 'uploadDocument').mockRejectedValueOnce(
      new ApiError('Unsupported media', 415)
    );

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));
    const file = new File(['text'], 'test.pdf', { type: 'application/pdf' });
    fireEvent.change(document.getElementById('file-upload-input')!, { target: { files: [file] } });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Formato de archivo no soportado/i)).toBeInTheDocument();
    });
  });

  it('20. Error 422 muestra mensaje de documento no procesable', async () => {
    vi.spyOn(documentsApi, 'uploadDocument').mockRejectedValueOnce(
      new ApiError('No se pudo extraer texto', 422, 'No se pudo extraer texto del documento. Verifique que contiene texto digital legible.')
    );

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: /Desde archivo/i }));
    const file = new File(['text'], 'test.pdf', { type: 'application/pdf' });
    fireEvent.change(document.getElementById('file-upload-input')!, { target: { files: [file] } });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/No se pudo extraer texto del documento/i)).toBeInTheDocument();
    });
  });

  it('21. Error 500 muestra mensaje de error interno', async () => {
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockRejectedValueOnce(
      new ApiError('Internal server error', 500)
    );

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://example.com/err500.pdf' },
    });

    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Error interno del servidor al procesar el documento/i)).toBeInTheDocument();
    });
  });

  it('22. Tras éxito se refresca el listado en DocumentsPage', async () => {
    vi.spyOn(observatoryApi, 'getSources').mockResolvedValue([]);
    const getDocsSpy = vi.spyOn(documentsApi, 'getDocuments').mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });

    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockResolvedValueOnce({
      status: 'created',
      entry_id: 'doc-new-1',
      title: 'Nuevo Documento',
      url: 'https://example.com/nuevo.pdf',
      analysis_requested: false,
      analysis_scheduled: false,
      created_at: new Date().toISOString(),
    });

    render(
      <MemoryRouter initialEntries={['/documentos']}>
        <DocumentsPage />
      </MemoryRouter>
    );

    // Initial fetch on mount
    await waitFor(() => {
      expect(getDocsSpy).toHaveBeenCalledTimes(1);
    });

    // Open modal
    fireEvent.click(screen.getByRole('button', { name: /Añadir documento/i }));

    // Submit valid document
    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://example.com/nuevo.pdf' },
    });
    const submitBtn = screen.getAllByRole('button', { name: /Añadir documento/i }).find(b => b.getAttribute('type') === 'submit');
    fireEvent.click(submitBtn!);

    // List is refreshed (called second time)
    await waitFor(() => {
      expect(getDocsSpy).toHaveBeenCalledTimes(2);
    });
  });

  it('23. Tras éxito se puede cerrar el modal', async () => {
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockResolvedValueOnce({
      status: 'created',
      entry_id: 'doc-close-1',
      title: 'Documento Cierre',
      url: 'https://example.com/cierre.pdf',
      analysis_requested: false,
      analysis_scheduled: false,
      created_at: new Date().toISOString(),
    });

    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={onClose} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://example.com/cierre.pdf' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      expect(screen.getByText(/Documento añadido correctamente/i)).toBeInTheDocument();
    });

    // Click "Cerrar" button in success view
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('24. En 409 se ofrece acceder al documento existente mediante enlace correcto', async () => {
    vi.spyOn(documentsApi, 'ingestDocumentFromUrl').mockResolvedValueOnce({
      status: 'duplicate',
      existing_entry_id: 'existing-entry-uuid-777',
      duplicate_reason: 'url',
      message: 'El documento ya existe en el fondo documental.',
    });

    render(
      <MemoryRouter>
        <AddDocumentModal isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText(/URL del documento/i), {
      target: { value: 'https://example.com/dup.pdf' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Añadir documento$/i }));

    await waitFor(() => {
      const link = screen.getByRole('link', { name: /Ver documento existente/i });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', '/documentos/existing-entry-uuid-777');
    });
  });

  describe('Document Presentation & Formatting (Bloque 11C Presentation Fixes)', () => {
    it('25. formatDocumentTitle decodifica percent-encoding y elimina prefijos tecnicos y extension', () => {
      const rawCatTitle =
        '17497725%20Association%20of%20Consumer%20Support%20Organisations%20Ltd%20v%20Amazon.com%2C%20Inc%20%26%20Ors%20-%20Ruling%20%28CPO%20Decision%29%20%2013%20Oct%202026.pdf';
      const clean = formatDocumentTitle(rawCatTitle);

      expect(clean).not.toContain('%20');
      expect(clean).not.toContain('%26');
      expect(clean).not.toContain('%28');
      expect(clean).not.toContain('%29');
      expect(clean).not.toContain('%2C');
      expect(clean).not.toMatch(/\.pdf$/i);
      expect(clean).not.toMatch(/^17497725/);
      expect(clean).toBe(
        'Association of Consumer Support Organisations Ltd v Amazon.com, Inc & Ors - Ruling (CPO Decision) 13 Oct 2026'
      );
    });

    it('26. formatDocumentTitle preserva anos de cuatro digitos y limpia guiones bajos', () => {
      expect(formatDocumentTitle('2024_annual_competition_report.pdf')).toBe('2024 annual competition report');
      expect(formatDocumentTitle('a1b2c3d4e5f67890123456789abcdef0_decision.pdf')).toBe('decision');
      expect(formatDocumentTitle(null)).toBe('Expediente documental');
      expect(formatDocumentTitle('')).toBe('Expediente documental');
    });

    it('27. renderDocumentContent reflows parrafos suaves y preserva estructuras juridicas', () => {
      const legalText = `1. Este es el primer parrafo numerado de la resolucion. Tiene un salto suave
que continua en la siguiente linea.

2. Este es el segundo parrafo.
(a) Primera condicion subordinada.
(b) Segunda condicion subordinada.

BETWEEN:

THE ASSOCIATION AND THE DEFENDANTS

24`;

      render(<div>{renderDocumentContent(legalText)}</div>);

      // Paragraph unwrapping
      expect(
        screen.getByText(/Este es el primer parrafo numerado de la resolucion\. Tiene un salto suave que continua en la siguiente linea\./)
      ).toBeInTheDocument();

      // Numbered items and sub-items
      expect(screen.getByText(/1\. Este es el primer parrafo/)).toBeInTheDocument();
      expect(screen.getByText(/\(a\) Primera condicion subordinada\./)).toBeInTheDocument();
      expect(screen.getByText(/\(b\) Segunda condicion subordinada\./)).toBeInTheDocument();

      // Heading
      const heading = screen.getByRole('heading', { level: 3, name: 'BETWEEN:' });
      expect(heading).toBeInTheDocument();

      // Page marker
      expect(screen.getByText(/— Página 24 —/)).toBeInTheDocument();
    });

    it('28. DocumentsPage formatea titulos codificados por URL en las tarjetas', async () => {
      vi.spyOn(observatoryApi, 'getSources').mockResolvedValueOnce([]);
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          {
            id: 'doc-cat',
            title: '17497725%20Ruling%20%28Amazon%20Case%29.pdf',
            url: 'https://example.com/cat.pdf',
            source: { id: 's1', name: 'CAT' },
            has_analysis: false,
            is_linkedin: false,
            source_origin_category: 'direct_web',
            captured_at: '2026-10-13T10:00:00Z',
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('link', { name: 'Ruling (Amazon Case)' })).toBeInTheDocument();
      });
      expect(screen.queryByText(/17497725%20Ruling/)).not.toBeInTheDocument();
    });

    it('29. DocumentDetailPage formatea titulo y renderiza contenido con tipografia estructurada', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce({
        id: 'doc-detail-1',
        title: '17497725%20Ruling%20%28Amazon%20Case%29.pdf',
        url: 'https://example.com/cat.pdf',
        content: '1. Párrafo de la sentencia.\n(a) Cláusula primera.\n\nORDER:\nTribunal final order.',
        has_analysis: false,
        is_linkedin: false,
        source_origin_category: 'direct_web',
        source: { id: 's1', name: 'CAT' },
        captured_at: '2026-10-13T10:00:00Z',
      });

      render(
        <MemoryRouter initialEntries={['/documentos/doc-detail-1']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('heading', { level: 1, name: 'Ruling (Amazon Case)' })).toBeInTheDocument();
      });

      expect(screen.getByText(/1\. Párrafo de la sentencia\./)).toBeInTheDocument();
      expect(screen.getByText(/\(a\) Cláusula primera\./)).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 3, name: 'ORDER:' })).toBeInTheDocument();
      expect(screen.getByText(/Tribunal final order\./)).toBeInTheDocument();
    });

    it('30. healHistoricalContent sana fragmentos de palabras y signos de puntuacion en textos historicos', () => {
      const historical = [
        'Ne\nutral citation [2026] CAT 77',
        'Bef\nore:',
        'SI\nR PETER ROTH',
        'BETW\nEEN:',
        'dis-\ntribution',
        'Porta\n, under the EU Merger Regulation\n.',
        "XXXLutz\n's proposed",
      ].join('\n');

      const healed = healHistoricalContent(historical);

      expect(healed).toContain('Neutral citation [2026] CAT 77');
      expect(healed).toContain('Before:');
      expect(healed).toContain('SIR PETER ROTH');
      expect(healed).toContain('BETWEEN:');
      expect(healed).toContain('distribution');
      expect(healed).toContain('Porta, under the EU Merger Regulation.');
      expect(healed).toContain("XXXLutz's proposed");
    });

    it('31. renderDocumentContent no genera encabezados h3 falsos para fragmentos como BETW o ore:', () => {
      const brokenFragments = `Ne
utral citation [2026] CAT 77

Bef
ore:

SI
R PETER ROTH

BETW
EEN:

THE ASSOCIATION OF CONSUMER SUPPORT ORGANISATIONS`;

      render(<div>{renderDocumentContent(brokenFragments)}</div>);

      // Should heal and render legitimate headings
      expect(screen.getByRole('heading', { level: 3, name: 'BETWEEN:' })).toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 3, name: 'Before:' })).toBeInTheDocument();

      // Should NEVER render broken fragments as headers
      expect(screen.queryByRole('heading', { level: 3, name: 'BETW' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { level: 3, name: 'EEN:' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { level: 3, name: 'ore:' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { level: 3, name: 'Bef' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { level: 3, name: 'SI' })).not.toBeInTheDocument();

      // Normal text paragraphs should be present
      expect(screen.getByText(/Neutral citation \[2026\] CAT 77/)).toBeInTheDocument();
      expect(screen.getByText(/SIR PETER ROTH/)).toBeInTheDocument();
    });

    it('32. renderDocumentContent reflows texto de la Comision Europea sin saltos inline', () => {
      const ecBroken = [
        'The European Commission has opened an in-depth investigation to assess',
        'XXXLutz',
        "'s proposed",
        'acquisition of',
        'Porta',
        ', under the',
        'EU Merger Regulation',
        '.',
      ].join('\n');

      render(<div>{renderDocumentContent(ecBroken)}</div>);

      expect(
        screen.getByText(/assess XXXLutz's proposed acquisition of Porta, under the EU Merger Regulation\./)
      ).toBeInTheDocument();
    });
  });
});

describe('Word Document Export Integration (Bloque 12 Fase 3)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    if (!window.URL.createObjectURL) {
      window.URL.createObjectURL = vi.fn();
    }
    if (!window.URL.revokeObjectURL) {
      window.URL.revokeObjectURL = vi.fn();
    }
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const mockAnalysedDoc = {
    id: 'doc-export-analysed',
    title: 'Resolución Sancionadora CNMC: Cártel de Fabricantes',
    url: 'https://cnmc.example.org/res-101',
    canonical_url: 'https://cnmc.example.org/res-101',
    author: 'CNMC',
    published_at: '2026-10-01T10:00:00Z',
    captured_at: '2026-10-01T10:00:00Z',
    source: { id: 'src-cnmc', name: 'CNMC', tracked_entity_name: 'Consejo CNMC' },
    is_linkedin: false,
    source_origin_category: 'institutional',
    content: 'Texto íntegro de la resolución sancionadora por cártel colusorio.',
    excerpt: 'Extracto de la resolución sancionadora...',
    has_analysis: true,
    analysis: {
      id: 'ana-export-1',
      status: 'completed',
      relevance_status: 'relevant' as const,
      relevance_score: 96,
      summary: 'Dictamen de relevancia jurídica confirmando infracción continuada.',
      key_points: ['Infracción grave art. 1 LDC'],
      canonical_topics: [{ code: 'antitrust', name: 'Antitrust' }],
    },
    evidence: {
      source: 'deep',
      summary_quotes: [{ source_field: 'content', quote: 'cártel colusorio' }],
      key_points: [],
    },
  };

  const mockUnanalysedDoc = {
    id: 'doc-export-unanalysed',
    title: 'Consulta Pública Previa MITECO',
    url: 'https://miteco.example.org/consulta-202',
    canonical_url: 'https://miteco.example.org/consulta-202',
    author: 'MITECO',
    published_at: '2026-10-05T10:00:00Z',
    captured_at: '2026-10-05T10:00:00Z',
    source: { id: 'src-miteco', name: 'MITECO' },
    is_linkedin: false,
    source_origin_category: 'institutional',
    content: 'Texto original de la consulta pública sin análisis posterior.',
    has_analysis: false,
    analysis: null,
    evidence: null,
  };

  // ===========================================================================
  // API Service Unit Tests (Punto 11)
  // ===========================================================================

  describe('documentsApi.exportDocument service', () => {
    it('parses Content-Disposition filename correctly', () => {
      expect(
        parseContentDispositionFilename('attachment; filename="dictamen-caso-123.docx"')
      ).toBe('dictamen-caso-123.docx');
      expect(
        parseContentDispositionFilename('attachment; filename=expediente-456.docx')
      ).toBe('expediente-456.docx');
      expect(
        parseContentDispositionFilename("attachment; filename*=UTF-8''resolucion-espanola.docx")
      ).toBe('resolucion-espanola.docx');
      expect(parseContentDispositionFilename(null)).toBeNull();
      expect(parseContentDispositionFilename('inline')).toBeNull();
    });

    it('performs GET to correct endpoint with credentials and returns blob and filename', async () => {
      const mockBlob = new Blob(['docx-content'], {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      });
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Headers({
          'content-type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          'content-disposition': 'attachment; filename="dictamen-prueba.docx"',
        }),
        blob: async () => mockBlob,
      } as unknown as Response);

      const result = await documentsApi.exportDocument('doc-test-123');

      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/documents/doc-test-123/export'),
        expect.objectContaining({
          method: 'GET',
          credentials: 'include',
        })
      );
      expect(result.filename).toBe('dictamen-prueba.docx');
      expect(result.blob).toBe(mockBlob);
    });

    it('handles 401 Unauthorized by dispatching hitchings:unauthorized and throwing ApiError', async () => {
      const eventSpy = vi.fn();
      window.addEventListener('hitchings:unauthorized', eventSpy);

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Not authenticated' }),
      } as unknown as Response);

      await expect(documentsApi.exportDocument('doc-test-123')).rejects.toThrow(ApiError);
      expect(eventSpy).toHaveBeenCalled();
      window.removeEventListener('hitchings:unauthorized', eventSpy);
    });

    it('handles 404 Not Found by throwing ApiError with status 404', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: async () => ({ detail: "Documento 'doc-999' no encontrado" }),
      } as unknown as Response);

      await expect(documentsApi.exportDocument('doc-999')).rejects.toMatchObject({
        status: 404,
        detail: "Documento 'doc-999' no encontrado",
      });
    });

    it('handles 500 Internal Server Error by throwing ApiError with status 500', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'Internal server error' }),
      } as unknown as Response);

      await expect(documentsApi.exportDocument('doc-500')).rejects.toMatchObject({
        status: 500,
      });
    });

    it('triggerBrowserDownload creates anchor, triggers click and revokes object URL', () => {
      const createObjectURLSpy = vi
        .spyOn(window.URL, 'createObjectURL')
        .mockReturnValue('blob:http://localhost/fake-uuid');
      const revokeObjectURLSpy = vi
        .spyOn(window.URL, 'revokeObjectURL')
        .mockImplementation(() => {});

      const clickSpy = vi.fn();
      const origCreateElement = document.createElement.bind(document);
      vi.spyOn(document, 'createElement').mockImplementation((tagName: string) => {
        const el = origCreateElement(tagName);
        if (tagName.toLowerCase() === 'a') {
          el.click = clickSpy;
        }
        return el;
      });

      const testBlob = new Blob(['test']);
      triggerBrowserDownload(testBlob, 'prueba.docx');

      expect(createObjectURLSpy).toHaveBeenCalledWith(testBlob);
      expect(clickSpy).toHaveBeenCalled();
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/fake-uuid');
    });
  });

  // ===========================================================================
  // UI Integration Tests (Punto 10)
  // ===========================================================================

  describe('DocumentDetailPage UI Export Button', () => {
    it('1. El boton "Exportar Word" aparece en la ficha', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Resolución Sancionadora CNMC: Cártel de Fabricantes')).toBeInTheDocument();
      });

      const exportBtn = screen.getByRole('button', { name: /Exportar Word/i });
      expect(exportBtn).toBeInTheDocument();
      expect(exportBtn).toBeEnabled();
    });

    it('2. El boton funciona para un documento con analisis IA', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      const exportSpy = vi.spyOn(documentsApi, 'exportDocument').mockResolvedValueOnce({
        blob: new Blob(['fake-docx']),
        filename: 'dictamen-resolucion-sancionadora-cnmc.docx',
      });
      vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:http://localhost/123');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(exportSpy).toHaveBeenCalledWith('doc-export-analysed');
        expect(
          screen.getByText(/Descarga iniciada: dictamen-resolucion-sancionadora-cnmc\.docx/i)
        ).toBeInTheDocument();
      });
    });

    it('3. El boton funciona para un documento sin analisis IA', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockUnanalysedDoc);
      const exportSpy = vi.spyOn(documentsApi, 'exportDocument').mockResolvedValueOnce({
        blob: new Blob(['fake-docx-unanalysed']),
        filename: 'expediente-consulta-publica-previa-miteco.docx',
      });
      vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:http://localhost/456');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-unanalysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(exportSpy).toHaveBeenCalledWith('doc-export-unanalysed');
        expect(
          screen.getByText(/Descarga iniciada: expediente-consulta-publica-previa-miteco\.docx/i)
        ).toBeInTheDocument();
      });
    });

    it('4. Al pulsar muestra loading "Generando Word..." y el boton queda temporalmente deshabilitado', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);

      let resolveExport: (val: { blob: Blob; filename: string }) => void;
      const exportPromise = new Promise<{ blob: Blob; filename: string }>((resolve) => {
        resolveExport = resolve;
      });
      vi.spyOn(documentsApi, 'exportDocument').mockReturnValueOnce(exportPromise);

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      const button = screen.getByRole('button', { name: /Exportar Word/i });
      fireEvent.click(button);

      // Verify loading state and disabled attribute
      await waitFor(() => {
        expect(screen.getByText(/Generando Word\.\.\./i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Generando Word\.\.\./i })).toBeDisabled();
      });

      // Complete export
      resolveExport!({ blob: new Blob(['ok']), filename: 'test.docx' });

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeEnabled();
      });
    });

    it('5 y 6. Procesa correctamente el Blob y utiliza el nombre devuelto por el backend', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      const testBlob = new Blob(['exact-binary-bytes']);
      const customFilename = 'dictamen-personalizado-cnmc.docx';

      vi.spyOn(documentsApi, 'exportDocument').mockResolvedValueOnce({
        blob: testBlob,
        filename: customFilename,
      });

      const createObjectUrlSpy = vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:http://test');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(createObjectUrlSpy).toHaveBeenCalledWith(testBlob);
        expect(screen.getByText(new RegExp(customFilename, 'i'))).toBeInTheDocument();
      });
    });

    it('7 y 8. Crea y dispara la descarga y libera el object URL', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      vi.spyOn(documentsApi, 'exportDocument').mockResolvedValueOnce({
        blob: new Blob(['bytes']),
        filename: 'dictamen-descarga.docx',
      });

      const createObjectUrlSpy = vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:http://download-url');
      const revokeObjectUrlSpy = vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(createObjectUrlSpy).toHaveBeenCalled();
        expect(revokeObjectUrlSpy).toHaveBeenCalledWith('blob:http://download-url');
      });
    });

    it('9. Error 401 muestra feedback de sesion expirada', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      vi.spyOn(documentsApi, 'exportDocument').mockRejectedValueOnce(
        new ApiError('Unauthorized', 401, 'Token expired')
      );

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /Sesión no válida o expirada\. Por favor, vuelva a iniciar sesión\./i
        );
      });
    });

    it('10. Error 404 muestra feedback de documento no encontrado', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      vi.spyOn(documentsApi, 'exportDocument').mockRejectedValueOnce(
        new ApiError('Not Found', 404, 'Documento no encontrado')
      );

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /El documento solicitado no existe en el fondo documental\./i
        );
      });
    });

    it('11. Error 500 muestra feedback de error interno del servidor', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      vi.spyOn(documentsApi, 'exportDocument').mockRejectedValueOnce(
        new ApiError('Internal Error', 500, 'Server crashed')
      );

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /Error interno del servidor al exportar el documento\. Inténtelo de nuevo más tarde\./i
        );
      });
    });

    it('12. El boton vuelve a estar habilitado despues de un error para permitir reintento', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      vi.spyOn(documentsApi, 'exportDocument').mockRejectedValueOnce(
        new ApiError('Server error', 500)
      );

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      const button = screen.getByRole('button', { name: /Exportar Word/i });
      fireEvent.click(button);

      await waitFor(() => {
        expect(screen.getByRole('alert')).toBeInTheDocument();
        expect(button).toBeEnabled();
      });
    });

    it('13. No se produce navegacion ni reload de la pagina al pulsar exportar', async () => {
      vi.spyOn(documentsApi, 'getDocument').mockResolvedValueOnce(mockAnalysedDoc);
      vi.spyOn(documentsApi, 'exportDocument').mockResolvedValueOnce({
        blob: new Blob(['fake-docx']),
        filename: 'dictamen.docx',
      });
      vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:http://test');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos/doc-export-analysed']}>
          <Routes>
            <Route path="/documentos/:documentId" element={<DocumentDetailPage />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Exportar Word/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Exportar Word/i }));

      await waitFor(() => {
        expect(screen.getByText(/Descarga iniciada/i)).toBeInTheDocument();
      });

      // Page stays on the same document view
      expect(screen.getByText('Resolución Sancionadora CNMC: Cártel de Fabricantes')).toBeInTheDocument();
      expect(screen.getByText('Cuerpo Documental Original')).toBeInTheDocument();
      expect(screen.getByText('Dictamen Jurídico IA')).toBeInTheDocument();
    });
  });

  describe('Dossier Export Multiple Selection (Bloque 12 Fase 4C Frontend)', () => {
    function createMockDocItem(id: string, title: string, hasAnalysis = true): DocumentListItem {
      return {
        id,
        title,
        url: `https://example.com/${id}`,
        source: { id: 'src-1', name: 'CNMC' },
        captured_at: '2026-09-01T10:00:00Z',
        published_at: '2026-09-01T10:00:00Z',
        is_linkedin: false,
        source_origin_category: 'institutional',
        has_analysis: hasAnalysis,
        analysis: hasAnalysis
          ? {
              id: `ana-${id}`,
              status: 'completed',
              relevance_status: 'relevant',
              relevance_score: 90,
              summary: `Resumen de ${title}`,
              key_points: ['Punto clave'],
              canonical_topics: [{ code: 'antitrust', name: 'Competencia' }],
            }
          : null,
      };
    }

    beforeEach(() => {
      vi.restoreAllMocks();
      vi.spyOn(observatoryApi, 'getSources').mockResolvedValue([
        { id: 'src-1', name: 'CNMC', type: 'website', entry_count: 10 },
      ]);
    });

    afterEach(() => {
      cleanup();
      vi.restoreAllMocks();
    });

    it('1. Renderizado de checkboxes en cada fila y checkbox maestro desmarcado inicialmente', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
          createMockDocItem('doc-3', 'Doc 3'),
        ],
        total: 3,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const master = screen.getByLabelText(
        'Seleccionar todos los documentos en esta página'
      ) as HTMLInputElement;
      expect(master).not.toBeChecked();
      expect(master.indeterminate).toBe(false);

      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).not.toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 2')).not.toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 3')).not.toBeChecked();

      expect(screen.queryByLabelText('Acciones de documentos seleccionados')).not.toBeInTheDocument();
    });

    it('2. Seleccionar un documento individual actualiza el contador a "1 documento seleccionado" y muestra la barra flotante', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
        ],
        total: 2,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const check1 = screen.getByLabelText('Seleccionar documento: Doc 1');
      fireEvent.click(check1);

      expect(check1).toBeChecked();
      expect(screen.getByLabelText('Acciones de documentos seleccionados')).toBeInTheDocument();
      expect(screen.getByText(/1 documento seleccionado/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Exportar Dossier Word/i })).toBeInTheDocument();
    });

    it('3. Deseleccionar un documento individual devuelve el contador a 0 y oculta la barra flotante', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const check1 = screen.getByLabelText('Seleccionar documento: Doc 1');
      fireEvent.click(check1);
      expect(screen.getByLabelText('Acciones de documentos seleccionados')).toBeInTheDocument();

      fireEvent.click(check1);
      expect(check1).not.toBeChecked();
      expect(screen.queryByLabelText('Acciones de documentos seleccionados')).not.toBeInTheDocument();
    });

    it('4. Seleccionar varios documentos individuales actualiza el contador reflejando la cantidad exacta', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
          createMockDocItem('doc-3', 'Doc 3'),
        ],
        total: 3,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 2'));

      expect(screen.getByText(/2 documentos seleccionados/i)).toBeInTheDocument();
    });

    it('5. Checkbox maestro "Seleccionar todos" marca todos los documentos visibles de la pagina actual', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
          createMockDocItem('doc-3', 'Doc 3'),
        ],
        total: 3,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const master = screen.getByLabelText(
        'Seleccionar todos los documentos en esta página'
      ) as HTMLInputElement;
      fireEvent.click(master);

      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 2')).toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 3')).toBeChecked();
      expect(master).toBeChecked();
      expect(screen.getByText(/3 documentos seleccionados/i)).toBeInTheDocument();
    });

    it('6. Checkbox maestro desmarca todos si ya estaban seleccionados todos en la pagina', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
        ],
        total: 2,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const master = screen.getByLabelText(
        'Seleccionar todos los documentos en esta página'
      ) as HTMLInputElement;
      fireEvent.click(master);
      expect(master).toBeChecked();

      fireEvent.click(master);
      expect(master).not.toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).not.toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 2')).not.toBeChecked();
      expect(screen.queryByLabelText('Acciones de documentos seleccionados')).not.toBeInTheDocument();
    });

    it('7. Estado indeterminate del checkbox maestro cuando solo algunos documentos estan seleccionados', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
        ],
        total: 2,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const master = screen.getByLabelText(
        'Seleccionar todos los documentos en esta página'
      ) as HTMLInputElement;
      expect(master.indeterminate).toBe(false);

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      expect(master.indeterminate).toBe(true);
      expect(master.checked).toBe(false);

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 2'));
      expect(master.indeterminate).toBe(false);
      expect(master.checked).toBe(true);
    });

    it('8. Boton "Limpiar seleccion" en la barra flotante vacia la seleccion y oculta la barra', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
        ],
        total: 2,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      const floatingBar = screen.getByLabelText('Acciones de documentos seleccionados');
      expect(floatingBar).toBeInTheDocument();

      const clearBtn = floatingBar.querySelector('button');
      expect(clearBtn).toHaveTextContent(/Limpiar selección/i);
      fireEvent.click(clearBtn!);

      expect(screen.queryByLabelText('Acciones de documentos seleccionados')).not.toBeInTheDocument();
      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).not.toBeChecked();
    });

    it('9. Boton "Limpiar seleccion" en la barra superior vacia la seleccion', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      const clearBtns = screen.getAllByRole('button', { name: /^Limpiar selección$/i });
      fireEvent.click(clearBtns[0]);

      expect(screen.queryByLabelText('Acciones de documentos seleccionados')).not.toBeInTheDocument();
      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).not.toBeChecked();
    });

    it('10. Limite maximo de 30 documentos: checkboxes de documentos no seleccionados quedan deshabilitados', async () => {
      const items = Array.from({ length: 32 }, (_, i) =>
        createMockDocItem(`doc-${i + 1}`, `Documento ${i + 1}`)
      );
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items,
        total: 32,
        limit: 40,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Documento 1')).toBeInTheDocument();
      });

      for (let i = 1; i <= 30; i++) {
        fireEvent.click(screen.getByLabelText(`Seleccionar documento: Documento ${i}`));
      }

      expect(screen.getByText(/30 documentos seleccionados/i)).toBeInTheDocument();
      expect(screen.getByText(/Límite 30 alcanzado/i)).toBeInTheDocument();

      const doc31 = screen.getByLabelText('Seleccionar documento: Documento 31') as HTMLInputElement;
      const doc32 = screen.getByLabelText('Seleccionar documento: Documento 32') as HTMLInputElement;
      expect(doc31).toBeDisabled();
      expect(doc32).toBeDisabled();

      const doc1 = screen.getByLabelText('Seleccionar documento: Documento 1') as HTMLInputElement;
      expect(doc1).not.toBeDisabled();

      fireEvent.click(doc1);
      expect(doc31).not.toBeDisabled();
      expect(doc32).not.toBeDisabled();
    });

    it('11. "Seleccionar todos en esta pagina" respeta el limite maximo de 30 si la pagina contiene mas de 30', async () => {
      const items = Array.from({ length: 35 }, (_, i) =>
        createMockDocItem(`doc-${i + 1}`, `Doc ${i + 1}`)
      );
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items,
        total: 35,
        limit: 40,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      const master = screen.getByLabelText('Seleccionar todos los documentos en esta página');
      fireEvent.click(master);

      expect(screen.getByText(/30 documentos seleccionados/i)).toBeInTheDocument();
      expect(
        screen.getByText(/Se han seleccionado documentos hasta completar el límite máximo de 30/i)
      ).toBeInTheDocument();
    });

    it('12. La seleccion se preserva entre navegacion de paginas (paginacion con offset)', async () => {
      const page1Items = [createMockDocItem('doc-1', 'Doc 1'), createMockDocItem('doc-2', 'Doc 2')];
      const page2Items = [createMockDocItem('doc-3', 'Doc 3'), createMockDocItem('doc-4', 'Doc 4')];

      vi.spyOn(documentsApi, 'getDocuments').mockImplementation(async (params) => {
        const offset = params?.offset || 0;
        return {
          items: offset === 0 ? page1Items : page2Items,
          total: 4,
          limit: 2,
          offset,
        };
      });

      render(
        <MemoryRouter initialEntries={['/documentos?limit=2']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      expect(screen.getByText(/1 documento seleccionado/i)).toBeInTheDocument();

      const nextBtn = screen.getByRole('button', { name: /Siguiente/i });
      fireEvent.click(nextBtn);

      await waitFor(() => {
        expect(screen.getByText('Doc 3')).toBeInTheDocument();
      });

      expect(screen.getByText(/1 documento seleccionado/i)).toBeInTheDocument();

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 3'));
      expect(screen.getByText(/2 documentos seleccionados/i)).toBeInTheDocument();

      const prevBtn = screen.getByRole('button', { name: /Anterior/i });
      fireEvent.click(prevBtn);

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).toBeChecked();
      expect(screen.getByLabelText('Seleccionar documento: Doc 2')).not.toBeChecked();
      expect(screen.getByText(/2 documentos seleccionados/i)).toBeInTheDocument();
    });

    it('13. La seleccion se preserva al cambiar filtros', async () => {
      const allItems = [createMockDocItem('doc-1', 'Doc 1', true), createMockDocItem('doc-2', 'Doc 2', false)];
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValue({
        items: allItems,
        total: 2,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      expect(screen.getByText(/1 documento seleccionado/i)).toBeInTheDocument();

      const statusSelect = screen.getByDisplayValue('Todos los estados');
      fireEvent.change(statusSelect, { target: { value: 'with_analysis' } });

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).toBeChecked();
      expect(screen.getByText(/1 documento seleccionado/i)).toBeInTheDocument();
    });

    it('14. Clic en "Exportar Dossier Word" en la barra flotante abre el modal ExportDossierModal', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));

      const exportBtn = screen.getByRole('button', { name: /Exportar Dossier Word/i });
      fireEvent.click(exportBtn);

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'Exportar Dossier Word' })).toBeInTheDocument();
      expect(screen.getByText(/Compilación temática de 1 expediente/i)).toBeInTheDocument();
    });

    it('15. El modal presenta el titulo por defecto sugerido "Dossier documental HITCHINGS"', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      const input = screen.getByLabelText(/Título del dossier/i) as HTMLInputElement;
      expect(input.value).toBe('Dossier documental HITCHINGS');
    });

    it('16. Cancelar el modal lo cierra sin disparar ninguna llamada a la API ni perder la seleccion', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      const exportSpy = vi.spyOn(documentsApi, 'exportDossierDocx');

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      const cancelBtn = screen.getByRole('button', { name: /Cancelar/i });
      fireEvent.click(cancelBtn);

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(exportSpy).not.toHaveBeenCalled();
      expect(screen.getByText(/1 documento seleccionado/i)).toBeInTheDocument();
    });

    it('17. Cerrar el modal mediante la tecla Escape lo cierra sin llamar a la API ni perder la seleccion', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      const exportSpy = vi.spyOn(documentsApi, 'exportDossierDocx');

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      fireEvent.keyDown(window, { key: 'Escape' });

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(exportSpy).not.toHaveBeenCalled();
    });

    it('18. El usuario puede modificar el titulo del dossier en el modal', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      const input = screen.getByLabelText(/Título del dossier/i) as HTMLInputElement;
      fireEvent.change(input, { target: { value: 'Dossier Monográfico Sector Lácteo' } });
      expect(input.value).toBe('Dossier Monográfico Sector Lácteo');
    });

    it('19. Confirmar el modal con titulo personalizado llama a exportDossierDocx con IDs ordenados y titulo', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [
          createMockDocItem('doc-1', 'Doc 1'),
          createMockDocItem('doc-2', 'Doc 2'),
        ],
        total: 2,
        limit: 20,
        offset: 0,
      });
      const fakeBlob = new Blob(['docx']);
      (fakeBlob as any).filename = 'dossier-lacteo.docx';
      const exportSpy = vi.spyOn(documentsApi, 'exportDossierDocx').mockResolvedValueOnce(fakeBlob);
      vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:mock');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 2'));
      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));

      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      const input = screen.getByLabelText(/Título del dossier/i);
      fireEvent.change(input, { target: { value: 'Dossier Monográfico Sector Lácteo' } });

      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(exportSpy).toHaveBeenCalledWith(
          ['doc-2', 'doc-1'],
          'Dossier Monográfico Sector Lácteo'
        );
      });
    });

    it('20. Confirmar el modal con titulo vacio llama a exportDossierDocx con titulo undefined', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      const fakeBlob = new Blob(['docx']);
      const exportSpy = vi.spyOn(documentsApi, 'exportDossierDocx').mockResolvedValueOnce(fakeBlob);
      vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:mock');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      const input = screen.getByLabelText(/Título del dossier/i);
      fireEvent.change(input, { target: { value: '   ' } });

      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(exportSpy).toHaveBeenCalledWith(['doc-1'], undefined);
      });
    });

    it('21. Durante la generacion se muestra "Generando dossier..." y los controles quedan deshabilitados', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(documentsApi, 'exportDossierDocx').mockReturnValue(new Promise(() => {}));

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      const submitBtn = screen.getByRole('button', { name: /Generar Dossier Word/i });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(screen.getByText(/Generando dossier\.\.\./i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Generando dossier\.\.\./i })).toBeDisabled();
        expect(screen.getByRole('button', { name: /Cancelar/i })).toBeDisabled();
        expect(screen.getByLabelText(/Título del dossier/i)).toBeDisabled();
      });
    });

    it('22. Exportacion exitosa descarga el blob, cierra el modal, muestra banner de exito y limpia la seleccion', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      const fakeBlob = new Blob(['docx content']);
      (fakeBlob as any).filename = 'dossier-descargado.docx';
      vi.spyOn(documentsApi, 'exportDossierDocx').mockResolvedValueOnce(fakeBlob);
      vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:mock');
      vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));

      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(
          screen.getByText(/Dossier generado correctamente: dossier-descargado\.docx/i)
        ).toBeInTheDocument();
      });

      expect(screen.queryByLabelText('Acciones de documentos seleccionados')).not.toBeInTheDocument();
      expect(screen.getByLabelText('Seleccionar documento: Doc 1')).not.toBeChecked();
    });

    it('23. Error 401 (sesion expirada) muestra feedback amigable de reautenticacion en el modal', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(documentsApi, 'exportDossierDocx').mockRejectedValueOnce(
        new ApiError('Unauthorized', 401)
      );

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));
      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /Tu sesión ha expirado\. Inicia sesión de nuevo\./i
        );
      });

      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('24. Error 404 (documento no encontrado en BD) muestra mensaje amigable en el modal', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(documentsApi, 'exportDossierDocx').mockRejectedValueOnce(
        new ApiError(
          'Not found',
          404,
          'Uno o varios documentos seleccionados ya no están disponibles en el Fondo Documental.'
        )
      );

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));
      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /Uno o varios documentos seleccionados ya no están disponibles en el Fondo Documental\./i
        );
      });
    });

    it('25. Error 422 (validacion o mas de 30 documentos) muestra mensaje amigable en el modal', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(documentsApi, 'exportDossierDocx').mockRejectedValueOnce(
        new ApiError(
          'Validation Error',
          422,
          'Los documentos seleccionados no cumplen las condiciones para generar el dossier.'
        )
      );

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));
      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /Los documentos seleccionados no cumplen las condiciones para generar el dossier\./i
        );
      });
    });

    it('26. Error 500 (error interno de servidor) muestra mensaje generico amigable en el modal', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(documentsApi, 'exportDossierDocx').mockRejectedValueOnce(
        new ApiError('Internal Error', 500)
      );

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));
      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent(
          /No se ha podido generar el dossier\. Inténtalo de nuevo\./i
        );
      });
    });

    it('27. Escribir en el campo de titulo tras un error limpia el mensaje de error', async () => {
      vi.spyOn(documentsApi, 'getDocuments').mockResolvedValueOnce({
        items: [createMockDocItem('doc-1', 'Doc 1')],
        total: 1,
        limit: 20,
        offset: 0,
      });
      vi.spyOn(documentsApi, 'exportDossierDocx').mockRejectedValueOnce(
        new ApiError('Internal Error', 500)
      );

      render(
        <MemoryRouter initialEntries={['/documentos']}>
          <DocumentsPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Doc 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByLabelText('Seleccionar documento: Doc 1'));
      fireEvent.click(screen.getByRole('button', { name: /Exportar Dossier Word/i }));
      fireEvent.click(screen.getByRole('button', { name: /Generar Dossier Word/i }));

      await waitFor(() => {
        expect(screen.getByRole('alert')).toBeInTheDocument();
      });

      const input = screen.getByLabelText(/Título del dossier/i);
      fireEvent.change(input, { target: { value: 'Nuevo título limpio' } });

      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('28. ExportDossierModal componente aislado valida props error y onClearError', () => {
      const handleClear = vi.fn();
      const handleClose = vi.fn();
      const handleConfirm = vi.fn();

      render(
        <ExportDossierModal
          isOpen={true}
          onClose={handleClose}
          selectedCount={5}
          onConfirm={handleConfirm}
          isGenerating={false}
          error="Error forzado de prueba"
          onClearError={handleClear}
        />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Error forzado de prueba')).toBeInTheDocument();
      expect(screen.getByText(/5 expedientes/i)).toBeInTheDocument();

      const input = screen.getByLabelText(/Título del dossier/i);
      fireEvent.change(input, { target: { value: 'Otro título' } });
      expect(handleClear).toHaveBeenCalled();
    });

    it('29. parseContentDispositionFilename extrae nombres de archivo con o sin comillas y maneja null', () => {
      expect(
        parseContentDispositionFilename('attachment; filename="dossier-cnmc.docx"')
      ).toBe('dossier-cnmc.docx');
      expect(
        parseContentDispositionFilename('attachment; filename=dossier-cnmc.docx')
      ).toBe('dossier-cnmc.docx');
      expect(
        parseContentDispositionFilename("attachment; filename*=UTF-8''dossier%20utf8.docx")
      ).toBe('dossier utf8.docx');
      expect(parseContentDispositionFilename(null)).toBeNull();
      expect(parseContentDispositionFilename('')).toBeNull();
    });

    it('30. mapDossierExportErrorMessage maneja correctamente errores de red, ApiError y tipos desconocidos', () => {
      expect(mapDossierExportErrorMessage(new ApiError('test', 401))).toBe(
        'Tu sesión ha expirado. Inicia sesión de nuevo.'
      );
      expect(
        mapDossierExportErrorMessage(
          new ApiError(
            'test',
            404,
            'Uno o varios documentos seleccionados ya no están disponibles en el Fondo Documental.'
          )
        )
      ).toBe('Uno o varios documentos seleccionados ya no están disponibles en el Fondo Documental.');
      expect(
        mapDossierExportErrorMessage(
          new ApiError(
            'test',
            422,
            'Los documentos seleccionados no cumplen las condiciones para generar el dossier.'
          )
        )
      ).toBe('Los documentos seleccionados no cumplen las condiciones para generar el dossier.');
      expect(mapDossierExportErrorMessage(new ApiError('test', 500))).toBe(
        'No se ha podido generar el dossier. Inténtalo de nuevo.'
      );
      expect(mapDossierExportErrorMessage(new Error('Fallo de red'))).toBe('Fallo de red');
      expect(mapDossierExportErrorMessage('error no estándar')).toBe(
        'No se ha podido generar el dossier. Inténtalo de nuevo.'
      );
    });
  });
});
