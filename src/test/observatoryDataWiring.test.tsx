import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { EntryDetailPage } from '../pages/EntryDetailPage';
import * as apiModule from '../services/observatoryApi';

// Vite raw imports for structural source code checks
import dashboardRaw from '../pages/DashboardPage.tsx?raw';
import observatoryRaw from '../pages/ObservatoryPage.tsx?raw';
import detailRaw from '../pages/EntryDetailPage.tsx?raw';

describe('Observatory Data Wiring & Anti-Regression Protections', () => {
  it('ensures DashboardPage does not import mock data directly', () => {
    expect(dashboardRaw).not.toContain('mockObservatoryData');
    expect(dashboardRaw).not.toContain('MOCK_');
    expect(dashboardRaw).not.toContain('../data/');
    expect(dashboardRaw).toContain('observatoryApi');
  });

  it('ensures ObservatoryPage does not import mock data directly', () => {
    expect(observatoryRaw).not.toContain('mockObservatoryData');
    expect(observatoryRaw).not.toContain('MOCK_');
    expect(observatoryRaw).not.toContain('../data/');
    expect(observatoryRaw).toContain('observatoryApi');
  });

  it('ensures EntryDetailPage does not import mock data directly', () => {
    expect(detailRaw).not.toContain('mockObservatoryData');
    expect(detailRaw).not.toContain('MOCK_');
    expect(detailRaw).not.toContain('../data/');
    expect(detailRaw).toContain('observatoryApi');
  });

  it('ensures all key points from API response are rendered in Detail without truncation', async () => {
    const sampleEntryWith5KeyPoints = {
      entry_id: '27c1a107-ebfe-40d0-ba9e-d7d399c3c565',
      title: 'Case C-60/25 [Livronsa] | Judgment',
      source: { id: 'source-1', name: 'Court of Justice of the European Union' },
      published_at: '2026-09-03T09:00:00Z',
      url: 'https://curia.europa.eu',
      content_type: 'judgment',
      relevance: { status: 'relevant' as const, score: 95 },
      summary: 'Resumen jurídico de Livronsa.',
      canonical_topics: [{ code: 'abuse_dominance', name: 'Abuso de posición de dominio' }],
      canonical_primary_topic: { code: 'abuse_dominance', name: 'Abuso de posición de dominio' },
      key_points: [
        'Punto clave 1: Límites objetivos del artículo 101 TFUE.',
        'Punto clave 2: Alcance del efecto vinculante del Reglamento 1/2003.',
        'Punto clave 3: Inexistencia de prueba automática sobre índice Euribor.',
        'Punto clave 4: Falta de vinculación instrumental en contrato hipotecario.',
        'Punto clave 5: Remisión al derecho nacional para consecuencias reflejas.',
      ],
      evidence: {
        source: 'deep',
        summary_quotes: [{ source_field: 'content', quote: 'Summary quote 1' }],
        key_points: [
          { point: 'Punto clave 1: Límites objetivos del artículo 101 TFUE.', quotes: [] },
          { point: 'Punto clave 2: Alcance del efecto vinculante del Reglamento 1/2003.', quotes: [] },
          { point: 'Punto clave 3: Inexistencia de prueba automática sobre índice Euribor.', quotes: [] },
          { point: 'Punto clave 4: Falta de vinculación instrumental en contrato hipotecario.', quotes: [] },
          { point: 'Punto clave 5: Remisión al derecho nacional para consecuencias reflejas.', quotes: [] },
        ],
      },
    };

    vi.spyOn(apiModule.observatoryApi, 'getEntry').mockResolvedValueOnce(sampleEntryWith5KeyPoints);

    render(
      <MemoryRouter initialEntries={['/observatorio/27c1a107-ebfe-40d0-ba9e-d7d399c3c565']}>
        <Routes>
          <Route path="/observatorio/:entryId" element={<EntryDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    // Verify presence of heading
    expect(await screen.findByText('Puntos Clave del Pronunciamiento')).toBeInTheDocument();

    // All 5 key points must be rendered in DOM
    const matches1 = await screen.findAllByText(/Punto clave 1:/);
    expect(matches1.length).toBeGreaterThanOrEqual(1);

    const matches2 = screen.getAllByText(/Punto clave 2:/);
    expect(matches2.length).toBeGreaterThanOrEqual(1);

    const matches3 = screen.getAllByText(/Punto clave 3:/);
    expect(matches3.length).toBeGreaterThanOrEqual(1);

    const matches4 = screen.getAllByText(/Punto clave 4:/);
    expect(matches4.length).toBeGreaterThanOrEqual(1);

    const matches5 = screen.getAllByText(/Punto clave 5:/);
    expect(matches5.length).toBeGreaterThanOrEqual(1);
  });

  it('renders exact Gormsen title and score without substitution or mock fallback', async () => {
    const gormsenEntry = {
      entry_id: 'ada5d125-a861-4ff8-bcf6-2b2607afd834',
      title: '[2026] EWCA Civ 993 | Dr Liza Lovdahl Gormsen v Meta Platforms, Inc. and Others - Judgment of the Court of Appeal (Pleading Amendments)',
      source: { id: 'source-cat', name: 'Competition Appeal Tribunal - Judgments' },
      published_at: '2026-07-29T14:30:00Z',
      url: 'https://catribunal.org.uk',
      content_type: 'judicial_decision',
      relevance: { status: 'relevant' as const, score: 95 },
      summary: 'El Tribunal de Apelación desestima el recurso de Meta.',
      canonical_topics: [{ code: 'collective_actions', name: 'Acciones colectivas' }],
      canonical_primary_topic: { code: 'collective_actions', name: 'Acciones colectivas' },
      key_points: ['Punto 1'],
      evidence: { source: 'deep', summary_quotes: [], key_points: [] },
    };

    vi.spyOn(apiModule.observatoryApi, 'getEntry').mockResolvedValueOnce(gormsenEntry);

    render(
      <MemoryRouter initialEntries={['/observatorio/ada5d125-a861-4ff8-bcf6-2b2607afd834']}>
        <Routes>
          <Route path="/observatorio/:entryId" element={<EntryDetailPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      '[2026] EWCA Civ 993 | Dr Liza Lovdahl Gormsen v Meta Platforms, Inc. and Others - Judgment of the Court of Appeal (Pleading Amendments)'
    );
    expect(screen.getByText('95')).toBeInTheDocument();
    expect(screen.queryByText(/Rachael Kent/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/1433\/5\/7\/22/i)).not.toBeInTheDocument();
  });
});
