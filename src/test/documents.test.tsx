import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DocumentsPage } from '../pages/DocumentsPage';
import { DocumentDetailPage } from '../pages/DocumentDetailPage';
import { AddDocumentModal } from '../components/documents/AddDocumentModal';
import {
  documentsApi,
  ApiError,
  parseContentDispositionFilename,
  triggerBrowserDownload,
} from '../services/documentsApi';
import { observatoryApi } from '../services/observatoryApi';
import { formatDocumentTitle, renderDocumentContent, healHistoricalContent } from '../utils/documentFormatter';

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
});
