import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { DocumentsPage } from '../pages/DocumentsPage';
import { DocumentDetailPage } from '../pages/DocumentDetailPage';
import { documentsApi } from '../services/documentsApi';
import { observatoryApi } from '../services/observatoryApi';

describe('Documentary Archive Module (Bloque 11B Frontend)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
