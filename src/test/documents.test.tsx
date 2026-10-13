import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DocumentsPage } from '../pages/DocumentsPage';
import { DocumentDetailPage } from '../pages/DocumentDetailPage';
import { AddDocumentModal } from '../components/documents/AddDocumentModal';
import { documentsApi, ApiError } from '../services/documentsApi';
import { observatoryApi } from '../services/observatoryApi';

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
});
