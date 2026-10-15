import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { KeywordSettingsPage } from '../pages/KeywordSettingsPage';
import { trackingApi } from '../services/trackingApi';
import { TrackingTopic } from '../types/tracking';

const MOCK_TOPICS: TrackingTopic[] = [
  {
    id: 'topic-1',
    matrix_id: 'matrix-1',
    code: 'cartels',
    name: 'Cárteles y acuerdos colusorios',
    description: 'Prácticas colusorias y cárteles secretos.',
    keywords: ['cartel', 'price fixing', '101 TFEU'],
    priority: 90,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'topic-2',
    matrix_id: 'matrix-1',
    code: 'dma_enforcement',
    name: 'Aplicación del Reglamento de Mercados Digitales (DMA)',
    description: 'Obligaciones de gatekeepers bajo el Reglamento DMA.',
    keywords: ['Digital Markets Act', 'DMA', 'Regulation (EU) 2022/1925', 'gatekeepers'],
    priority: 80,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'topic-3',
    matrix_id: 'matrix-1',
    code: 'ai_intellectual_property',
    name: 'IA y Propiedad Intelectual',
    description: 'Inteligencia artificial y derechos de autor.',
    keywords: ['IA', 'AI', 'copyright', 'propiedad intelectual'],
    priority: 70,
    active: true,
    provisional: false,
    created_at: '2026-10-14T12:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
];

describe('Keyword Settings Management Interface', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(trackingApi, 'getTopics').mockResolvedValue(JSON.parse(JSON.stringify(MOCK_TOPICS)));
  });

  it('1. Carga tópicos y selecciona el primero por defecto con sus keywords', async () => {
    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    // Initial loading indicator
    expect(screen.getByText(/cargando temas y palabras clave/i)).toBeInTheDocument();

    // Topics list loaded
    await waitFor(() => {
      expect(screen.getAllByText('Cárteles y acuerdos colusorios').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('Aplicación del Reglamento de Mercados Digitales (DMA)')).toBeInTheDocument();
      expect(screen.getByText('IA y Propiedad Intelectual')).toBeInTheDocument();
    });

    // Keywords of first topic displayed
    expect(screen.getByText('cartel')).toBeInTheDocument();
    expect(screen.getByText('price fixing')).toBeInTheDocument();
    expect(screen.getByText('101 TFEU')).toBeInTheDocument();
  });

  it('2. Permite seleccionar otro tópico y actualiza la lista de keywords', async () => {
    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Aplicación del Reglamento de Mercados Digitales (DMA)')).toBeInTheDocument();
    });

    // Click on DMA topic in sidebar
    fireEvent.click(screen.getByRole('heading', { level: 4, name: /aplicación del reglamento de mercados digitales/i }));

    // Should display DMA keywords
    await waitFor(() => {
      expect(screen.getByText('Digital Markets Act')).toBeInTheDocument();
      expect(screen.getByText('DMA')).toBeInTheDocument();
      expect(screen.getByText('Regulation (EU) 2022/1925')).toBeInTheDocument();
      expect(screen.getByText('gatekeepers')).toBeInTheDocument();
    });
  });

  it('3. Añadir nueva palabra clave con éxito y feedback visual', async () => {
    const updateSpy = vi.spyOn(trackingApi, 'updateTopicKeywords').mockResolvedValue({
      ...MOCK_TOPICS[0],
      keywords: [...(MOCK_TOPICS[0].keywords || []), 'bid rigging'],
    });

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('cartel')).toBeInTheDocument();
    });

    // Click on "+ Añadir palabra clave"
    const addBtn = screen.getByRole('button', { name: /\+ añadir palabra clave/i });
    fireEvent.click(addBtn);

    // Input appears
    const input = screen.getByPlaceholderText(/escribir nueva palabra clave/i);
    fireEvent.change(input, { target: { value: 'bid rigging' } });

    // Submit
    const saveBtn = screen.getByRole('button', { name: /guardar/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith('topic-1', ['cartel', 'price fixing', '101 TFEU', 'bid rigging']);
      expect(screen.getByText('Palabra clave añadida correctamente.')).toBeInTheDocument();
      expect(screen.getByText('bid rigging')).toBeInTheDocument();
    });
  });

  it('4. Modificar una palabra clave existente y guardar con éxito', async () => {
    const updateSpy = vi.spyOn(trackingApi, 'updateTopicKeywords').mockResolvedValue({
      ...MOCK_TOPICS[0],
      keywords: ['carteles secretos', 'price fixing', '101 TFEU'],
    });

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('cartel')).toBeInTheDocument();
    });

    // Click edit on 'cartel'
    const editBtn = screen.getByRole('button', { name: /editar cartel/i });
    fireEvent.click(editBtn);

    // Edit input with current value
    const editInput = screen.getByDisplayValue('cartel');
    fireEvent.change(editInput, { target: { value: 'carteles secretos' } });

    // Save
    const saveBtn = screen.getByRole('button', { name: /guardar/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith('topic-1', ['carteles secretos', 'price fixing', '101 TFEU']);
      expect(screen.getByText('Palabra clave actualizada correctamente.')).toBeInTheDocument();
      expect(screen.getByText('carteles secretos')).toBeInTheDocument();
    });
  });

  it('5. Cancelar la edición restaura la visualización sin guardar cambios', async () => {
    const updateSpy = vi.spyOn(trackingApi, 'updateTopicKeywords');

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('cartel')).toBeInTheDocument();
    });

    const editBtn = screen.getByRole('button', { name: /editar cartel/i });
    fireEvent.click(editBtn);

    const editInput = screen.getByDisplayValue('cartel');
    fireEvent.change(editInput, { target: { value: 'texto modificado pero cancelado' } });

    const cancelBtn = screen.getByRole('button', { name: /cancelar/i });
    fireEvent.click(cancelBtn);

    // Remains original and spy not called
    expect(screen.getByText('cartel')).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('6. Validación de campo vacío impide el guardado', async () => {
    const updateSpy = vi.spyOn(trackingApi, 'updateTopicKeywords');

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('cartel')).toBeInTheDocument();
    });

    const addBtn = screen.getByRole('button', { name: /\+ añadir palabra clave/i });
    fireEvent.click(addBtn);

    const input = screen.getByPlaceholderText(/escribir nueva palabra clave/i);
    fireEvent.change(input, { target: { value: '   ' } });

    const saveBtn = screen.getByRole('button', { name: /guardar/i });
    fireEvent.click(saveBtn);

    expect(screen.getByText('La palabra clave no puede estar vacía.')).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('7. Validación de palabra clave duplicada en el mismo tema', async () => {
    const updateSpy = vi.spyOn(trackingApi, 'updateTopicKeywords');

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('cartel')).toBeInTheDocument();
    });

    const addBtn = screen.getByRole('button', { name: /\+ añadir palabra clave/i });
    fireEvent.click(addBtn);

    const input = screen.getByPlaceholderText(/escribir nueva palabra clave/i);
    // 'cartel' already exists in topic-1
    fireEvent.change(input, { target: { value: 'cartel' } });

    const saveBtn = screen.getByRole('button', { name: /guardar/i });
    fireEvent.click(saveBtn);

    expect(screen.getByText('Esta palabra clave ya existe en este tema.')).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('8. Maneja errores del backend mostrando mensaje amigable al usuario', async () => {
    vi.spyOn(trackingApi, 'updateTopicKeywords').mockRejectedValue(new Error('Network error'));

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('cartel')).toBeInTheDocument();
    });

    const addBtn = screen.getByRole('button', { name: /\+ añadir palabra clave/i });
    fireEvent.click(addBtn);

    const input = screen.getByPlaceholderText(/escribir nueva palabra clave/i);
    fireEvent.change(input, { target: { value: 'nueva keyword valida' } });

    const saveBtn = screen.getByRole('button', { name: /guardar/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText('No se ha podido guardar el cambio.')).toBeInTheDocument();
    });
  });

  it('9. Búsqueda y filtrado dinámico local por tema y por keyword', async () => {
    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getAllByText('Cárteles y acuerdos colusorios').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('IA y Propiedad Intelectual')).toBeInTheDocument();
    });

    // Search by keyword 'copyright'
    const searchInput = screen.getByPlaceholderText(/buscar tema o palabra clave/i);
    fireEvent.change(searchInput, { target: { value: 'copyright' } });

    // The topic list filters to 'IA y Propiedad Intelectual' because it contains 'copyright'
    expect(screen.getAllByText('IA y Propiedad Intelectual').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole('heading', { level: 4, name: /cárteles/i })).not.toBeInTheDocument();
  });

  it('10. Mantiene el valor literal exacto de siglas, directivas y normativa', async () => {
    const updateSpy = vi.spyOn(trackingApi, 'updateTopicKeywords').mockResolvedValue({
      ...MOCK_TOPICS[1],
      keywords: [...(MOCK_TOPICS[1].keywords || []), 'Reglamento (UE) 2022/1925 - DMA'],
    });

    render(
      <MemoryRouter>
        <KeywordSettingsPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Aplicación del Reglamento de Mercados Digitales (DMA)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('heading', { level: 4, name: /aplicación del reglamento de mercados digitales/i }));

    await waitFor(() => {
      expect(screen.getByText('DMA')).toBeInTheDocument();
    });

    const addBtn = screen.getByRole('button', { name: /\+ añadir palabra clave/i });
    fireEvent.click(addBtn);

    const input = screen.getByPlaceholderText(/escribir nueva palabra clave/i);
    const literalNorm = 'Reglamento (UE) 2022/1925 - DMA';
    fireEvent.change(input, { target: { value: literalNorm } });

    const saveBtn = screen.getByRole('button', { name: /guardar/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      // Must preserve uppercase DMA, UE, and parentheses exactly
      expect(updateSpy).toHaveBeenCalledWith('topic-2', [
        'Digital Markets Act',
        'DMA',
        'Regulation (EU) 2022/1925',
        'gatekeepers',
        'Reglamento (UE) 2022/1925 - DMA',
      ]);
    });
  });
});
