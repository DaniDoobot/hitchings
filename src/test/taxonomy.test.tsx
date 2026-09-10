import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { DashboardPage } from '../pages/DashboardPage';
import { TaxonomyManagementModal } from '../components/taxonomy/TaxonomyManagementModal';
import { taxonomyApi } from '../services/taxonomyApi';
import { observatoryApi } from '../services/observatoryApi';
import { TaxonomyMatrixResponse } from '../types/taxonomy';

const MOCK_TAXONOMY_DATA: TaxonomyMatrixResponse = {
  matrix_id: 'mock-matrix-v01',
  code: 'HITCHINGS-v0.1',
  name: 'Matriz de Seguimiento HITCHINGS',
  status: 'active',
  updated_at: '2026-09-10T12:00:00Z',
  areas: [
    {
      id: 'area-1',
      code: 'derecho_competencia',
      name: 'Derecho de la Competencia',
      description: 'Regulación general de competencia',
      active: true,
      priority: 1,
      children: [
        {
          id: 'topic-101',
          code: 'carteles_acuerdos',
          name: 'Cárteles y acuerdos anticompetitivos',
          description: 'Prácticas colusorias',
          parent_id: 'area-1',
          parent_code: 'derecho_competencia',
          active: true,
          priority: 1,
        },
      ],
    },
    {
      id: 'area-2',
      code: 'aplicacion_privada',
      name: 'Aplicación Privada',
      description: 'Litigación de daños',
      active: true,
      priority: 2,
      children: [
        {
          id: 'topic-201',
          code: 'acciones_danos',
          name: 'Acciones de daños',
          description: null,
          parent_id: 'area-2',
          parent_code: 'aplicacion_privada',
          active: true,
          priority: 1,
        },
      ],
    },
  ],
};

describe('Editable Taxonomy Management (Bloque 11B)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(observatoryApi, 'isUsingMock').mockReturnValue(false);
  });

  it('renders "Gestionar áreas y temas" button on DashboardPage beside Top Topics', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    );

    const btn = await screen.findByRole('button', { name: /gestionar áreas y temas/i });
    expect(btn).toBeInTheDocument();
  });

  it('opens TaxonomyManagementModal from DashboardPage with disclaimer and matrix code', async () => {
    vi.spyOn(taxonomyApi, 'getTaxonomy').mockResolvedValue(MOCK_TAXONOMY_DATA);

    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    );

    const btn = await screen.findByRole('button', { name: /gestionar áreas y temas/i });
    fireEvent.click(btn);

    // Modal header
    expect(await screen.findByText('Gestión de Áreas y Temas')).toBeInTheDocument();
    expect(screen.getByText('HITCHINGS-v0.1')).toBeInTheDocument();

    // Institutional disclaimer
    expect(
      screen.getByText(/Las publicaciones ya analizadas no se reevaluarán automáticamente/i)
    ).toBeInTheDocument();

    // Áreas and child Temas
    expect(screen.getByText('Derecho de la Competencia')).toBeInTheDocument();
    expect(screen.getByText('Cárteles y acuerdos anticompetitivos')).toBeInTheDocument();
    expect(screen.getByText('Aplicación Privada')).toBeInTheDocument();
    expect(screen.getByText('Acciones de daños')).toBeInTheDocument();
  });

  it('handles optimistic concurrency conflict (HTTP 409) with informative banner and reload option', async () => {
    vi.spyOn(taxonomyApi, 'getTaxonomy').mockResolvedValue(MOCK_TAXONOMY_DATA);

    const conflictErr = new Error('La configuración ha sido modificada por otro usuario.') as Error & { status?: number };
    conflictErr.status = 409;
    vi.spyOn(taxonomyApi, 'createArea').mockRejectedValue(conflictErr);

    render(
      <TaxonomyManagementModal isOpen={true} onClose={() => {}} />
    );

    await screen.findByText('Derecho de la Competencia');

    // Click "Nueva Área"
    const newAreaBtn = screen.getByRole('button', { name: /nueva área/i });
    fireEvent.click(newAreaBtn);

    // Fill form
    const input = screen.getByPlaceholderText(/ej: Control de Ayudas de Estado/i);
    fireEvent.change(input, { target: { value: 'Nueva Área Concurrente' } });

    // Submit
    const submitBtn = screen.getByRole('button', { name: /crear área/i });
    fireEvent.click(submitBtn);

    // Concurrency conflict banner should appear
    await waitFor(() => {
      expect(
        screen.getByText(/La configuración ha sido modificada por otro usuario o sesión/i)
      ).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /recargar taxonomía/i })).toBeInTheDocument();
    });
  });

  it('allows searching and filtering areas and topics', async () => {
    vi.spyOn(taxonomyApi, 'getTaxonomy').mockResolvedValue(MOCK_TAXONOMY_DATA);

    render(
      <TaxonomyManagementModal isOpen={true} onClose={() => {}} />
    );

    await screen.findByText('Derecho de la Competencia');

    const searchInput = screen.getByPlaceholderText(/filtrar áreas o temas/i);
    fireEvent.change(searchInput, { target: { value: 'daños' } });

    // "Acciones de daños" should remain visible, "Cárteles" should be filtered out
    expect(screen.getByText('Acciones de daños')).toBeInTheDocument();
    expect(screen.queryByText('Cárteles y acuerdos anticompetitivos')).not.toBeInTheDocument();
  });
});
