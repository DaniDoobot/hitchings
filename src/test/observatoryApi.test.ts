import { describe, it, expect } from 'vitest';
import { ObservatoryApiService } from '../services/observatoryApi';

describe('ObservatoryApiService - Fail-Closed & Parameter Hardening', () => {
  it('builds query parameters accurately matching API contract 8A', () => {
    const api = new ObservatoryApiService({ apiBaseUrl: 'http://127.0.0.1:8000', useMock: false, isProd: false });
    const params = {
      limit: 25,
      offset: 50,
      sort_by: 'relevance_score' as const,
      sort_order: 'desc' as const,
      q: 'cartel',
      date_from: '2026-08-01',
      date_to: '2026-08-31',
      source_id: '97e685f0-6101-4433-8a03-9bb6fa6443c5',
      source_ids: ['id1', 'id2'],
      relevance_status: 'relevant' as const,
      min_relevance_score: 80,
      topic_code: 'damages_actions',
    };

    const query = api.buildQueryParams(params);

    expect(query.get('limit')).toBe('25');
    expect(query.get('offset')).toBe('50');
    expect(query.get('sort_by')).toBe('relevance_score');
    expect(query.get('sort_order')).toBe('desc');
    expect(query.get('q')).toBe('cartel');
    expect(query.get('date_from')).toBe('2026-08-01');
    expect(query.get('date_to')).toBe('2026-08-31');
    expect(query.get('source_id')).toBe('97e685f0-6101-4433-8a03-9bb6fa6443c5');
    expect(query.getAll('source_ids')).toEqual(['id1', 'id2']);
    expect(query.get('relevance_status')).toBe('relevant');
    expect(query.get('min_relevance_score')).toBe('80');
    expect(query.get('topic_code')).toBe('damages_actions');
  });

  it('fails closed when mock is disabled and VITE_API_BASE_URL is missing', async () => {
    const api = new ObservatoryApiService({ apiBaseUrl: '', useMock: false, isProd: false });

    expect(api.isUsingMock()).toBe(false);
    await expect(api.getDashboard()).rejects.toThrow('VITE_API_BASE_URL is required when mock data is disabled');
    await expect(api.getEntries()).rejects.toThrow('VITE_API_BASE_URL is required when mock data is disabled');
    await expect(api.getEntry('any-id')).rejects.toThrow('VITE_API_BASE_URL is required when mock data is disabled');
    await expect(api.getSources()).rejects.toThrow('VITE_API_BASE_URL is required when mock data is disabled');
    await expect(api.getTopics()).rejects.toThrow('VITE_API_BASE_URL is required when mock data is disabled');
  });

  it('supports same-origin relative API in production when VITE_API_BASE_URL is empty', () => {
    const api = new ObservatoryApiService({ apiBaseUrl: '', useMock: false, isProd: true });

    expect(api.isUsingMock()).toBe(false);
    expect(api.getBaseUrl()).toBe('');
  });

  it('strictly blocks mock data in production environment', async () => {
    const api = new ObservatoryApiService({ apiBaseUrl: 'http://127.0.0.1:8000', useMock: true, isProd: true });

    await expect(api.getDashboard()).rejects.toThrow(
      'Security Error: Mock data is strictly disabled in production environments.'
    );
  });

  it('allows mock data only in development when explicitly enabled', async () => {
    const api = new ObservatoryApiService({ apiBaseUrl: '', useMock: true, isProd: false });

    expect(api.isUsingMock()).toBe(true);
    const dashboard = await api.getDashboard();
    expect(dashboard).toBeDefined();
    expect(dashboard.total_publications).toBe(80);
    expect(dashboard.relevant_count).toBe(32);
    expect(dashboard.uncertain_count).toBe(10);
    expect(dashboard.not_relevant_count).toBe(38);
  });

  it('filters mock data correctly with pagination and topic hierarchy', async () => {
    const api = new ObservatoryApiService({ apiBaseUrl: '', useMock: true, isProd: false });

    // Filter relevant status
    const relevantRes = await api.getEntries({ relevance_status: 'relevant' });
    expect(relevantRes.items.every((i) => i.relevance.status === 'relevant')).toBe(true);

    // Topic hierarchical expansion: private_enforcement includes damages_actions and collective_actions
    const topicRes = await api.getEntries({ topic_code: 'private_enforcement' });
    expect(topicRes.items.length).toBeGreaterThan(0);

    // Pagination limit & offset
    const page1 = await api.getEntries({ limit: 2, offset: 0 });
    const page2 = await api.getEntries({ limit: 2, offset: 2 });
    expect(page1.items.length).toBe(2);
    expect(page2.items.length).toBe(2);
    expect(page1.items[0].entry_id).not.toBe(page2.items[0].entry_id);
  });
});
