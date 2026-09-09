import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { EntryDetailPage } from '../pages/EntryDetailPage';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import * as apiModule from '../services/observatoryApi';

describe('Common Observatory UI Components', () => {
  it('renders ErrorState with title, custom message and triggers onRetry', () => {
    const onRetryMock = vi.fn();
    render(
      <ErrorState
        title="Error de configuración"
        message="VITE_API_BASE_URL is required when mock data is disabled"
        onRetry={onRetryMock}
      />
    );

    expect(screen.getByText('Error de configuración')).toBeInTheDocument();
    expect(
      screen.getByText('VITE_API_BASE_URL is required when mock data is disabled')
    ).toBeInTheDocument();

    const retryBtn = screen.getByRole('button', { name: /reintentar/i });
    fireEvent.click(retryBtn);
    expect(onRetryMock).toHaveBeenCalledTimes(1);
  });

  it('renders EmptyState with reset action', () => {
    const onClearMock = vi.fn();
    render(
      <EmptyState
        title="Sin resultados"
        message="No se han encontrado resoluciones con los filtros aplicados"
        onClearFilters={onClearMock}
      />
    );

    expect(screen.getByText('Sin resultados')).toBeInTheDocument();
    expect(
      screen.getByText('No se han encontrado resoluciones con los filtros aplicados')
    ).toBeInTheDocument();

    const clearBtn = screen.getByRole('button', { name: /restablecer/i });
    fireEvent.click(clearBtn);
    expect(onClearMock).toHaveBeenCalledTimes(1);
  });

  it('renders 404 unavailable message when publication does not exist', async () => {
    vi.spyOn(apiModule.observatoryApi, 'getEntry').mockRejectedValueOnce(
      new Error('Publicación no encontrada o sin análisis vigente (ID: 00000000-0000-0000-0000-000000000000)')
    );

    render(
      <MemoryRouter initialEntries={['/observatorio/00000000-0000-0000-0000-000000000000']}>
        <Routes>
          <Route path="/observatorio/:entryId" element={<EntryDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText('Publicación no encontrada')).toBeInTheDocument();
    expect(screen.getByText(/Publicación no encontrada o sin análisis vigente/i)).toBeInTheDocument();
  });

  it('renders grounded evidence with general source document wording and NO internal audit jargon', async () => {
    const sampleEntry = {
      entry_id: 'test-entry-id',
      title: 'Asunto de Prueba sobre Abuso de Dominio',
      source: { id: 'source-id', name: 'Tribunal Competente' },
      published_at: '2026-09-01T10:00:00Z',
      url: 'https://example.org/decision/123',
      content_type: 'judgment',
      relevance: { status: 'relevant' as const, score: 95 },
      summary: 'Resumen jurídico de prueba para validación de vista.',
      canonical_topics: [{ code: 'abuse_dominance', name: 'Abuso de posición de dominio' }],
      canonical_primary_topic: { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
      key_points: ['Punto clave jurídico número 1.'],
      evidence: {
        source: 'deep',
        summary_quotes: [{ source_field: 'content', quote: 'Cita textual exacta del documento fuente.' }],
        key_points: [
          {
            point: 'Punto clave jurídico número 1.',
            quotes: [{ source_field: 'content', quote: 'Cita textual de respaldo para el punto clave.' }],
          },
        ],
      },
    };

    vi.spyOn(apiModule.observatoryApi, 'getEntry').mockResolvedValueOnce(sampleEntry);

    render(
      <MemoryRouter initialEntries={['/observatorio/test-entry-id']}>
        <Routes>
          <Route path="/observatorio/:entryId" element={<EntryDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    // Expect stately title & summary
    expect(
      await screen.findByRole('heading', { level: 1, name: /Asunto de Prueba sobre Abuso de Dominio/i })
    ).toBeInTheDocument();
    expect(screen.getByText('Resumen jurídico de prueba para validación de vista.')).toBeInTheDocument();

    // Verify sanitized, non-institutional specific wording
    expect(screen.getByText('Evidencias del Documento Fuente')).toBeInTheDocument();
    expect(screen.getByText('Citas del documento fuente')).toBeInTheDocument();
    expect(screen.getByText('Acceder a la publicación original')).toBeInTheDocument();

    // Verify external link has secure attributes
    const externalLink = screen.getByRole('link', { name: /acceder a la publicación original/i });
    expect(externalLink).toHaveAttribute('target', '_blank');
    expect(externalLink).toHaveAttribute('rel', 'noopener noreferrer');
    expect(externalLink).toHaveAttribute('href', 'https://example.org/decision/123');

    // Verify absence of internal audit fields
    expect(screen.queryByText(/GroundingValidator/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/raw_response/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/AnalysisCall/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pipeline_version/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/cost/i)).not.toBeInTheDocument();
  });
});
