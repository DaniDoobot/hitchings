import {
  ObservatoryDashboard,
  ObservatoryEntryDetail,
  ObservatoryListResponse,
  ObservatorySourceDetail,
  ObservatoryTopicNode,
  EntriesQueryParams,
} from '../types/observatory';
import {
  MOCK_DASHBOARD,
  MOCK_SOURCES,
  MOCK_TOPICS,
  filterMockEntries,
  getMockEntryDetail,
} from '../data/mockObservatoryData';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const FORCE_MOCK = import.meta.env.VITE_USE_MOCK_DATA === 'true';
const USE_MOCK = FORCE_MOCK || !API_BASE_URL;

class ObservatoryApiService {
  private isMockMode: boolean = USE_MOCK;

  constructor() {
    if (this.isMockMode) {
      console.info('[ObservatoryApi] Running in standalone MOCK DATA mode (no backend required).');
    } else {
      console.info(`[ObservatoryApi] Connected to real backend API: ${API_BASE_URL}`);
    }
  }

  public isUsingMock(): boolean {
    return this.isMockMode;
  }

  public async getDashboard(): Promise<ObservatoryDashboard> {
    if (this.isMockMode) {
      // Simulate minor async delay for realistic UX
      await new Promise(r => setTimeout(r, 120));
      return JSON.parse(JSON.stringify(MOCK_DASHBOARD));
    }

    const res = await fetch(`${API_BASE_URL}/api/v1/observatory/dashboard`);
    if (!res.ok) {
      throw new Error(`Error al obtener dashboard: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getSources(): Promise<ObservatorySourceDetail[]> {
    if (this.isMockMode) {
      await new Promise(r => setTimeout(r, 80));
      return JSON.parse(JSON.stringify(MOCK_SOURCES));
    }

    const res = await fetch(`${API_BASE_URL}/api/v1/observatory/sources`);
    if (!res.ok) {
      throw new Error(`Error al obtener fuentes: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getTopics(): Promise<ObservatoryTopicNode[]> {
    if (this.isMockMode) {
      await new Promise(r => setTimeout(r, 80));
      return JSON.parse(JSON.stringify(MOCK_TOPICS));
    }

    const res = await fetch(`${API_BASE_URL}/api/v1/observatory/topics`);
    if (!res.ok) {
      throw new Error(`Error al obtener taxonomía: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getEntries(params: EntriesQueryParams = {}): Promise<ObservatoryListResponse> {
    if (this.isMockMode) {
      await new Promise(r => setTimeout(r, 150));
      return filterMockEntries(params);
    }

    const query = new URLSearchParams();
    if (params.limit !== undefined) query.set('limit', String(params.limit));
    if (params.offset !== undefined) query.set('offset', String(params.offset));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.sort_order) query.set('sort_order', params.sort_order);
    if (params.q) query.set('q', params.q);
    if (params.date_from) query.set('date_from', params.date_from);
    if (params.date_to) query.set('date_to', params.date_to);
    if (params.source_id) query.set('source_id', params.source_id);
    if (params.relevance_status) query.set('relevance_status', params.relevance_status);
    if (params.min_relevance_score !== undefined && params.min_relevance_score !== null) {
      query.set('min_relevance_score', String(params.min_relevance_score));
    }
    if (params.topic_code) query.set('topic_code', params.topic_code);

    if (params.source_ids && params.source_ids.length > 0) {
      for (const sid of params.source_ids) {
        query.append('source_ids', sid);
      }
    }

    const res = await fetch(`${API_BASE_URL}/api/v1/observatory/entries?${query.toString()}`);
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || `Error al consultar publicaciones: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getEntry(entryId: string): Promise<ObservatoryEntryDetail> {
    if (this.isMockMode) {
      await new Promise(r => setTimeout(r, 100));
      const entry = getMockEntryDetail(entryId);
      if (!entry) {
        throw new Error(`Publicación no encontrada o sin análisis vigente (ID: ${entryId})`);
      }
      return JSON.parse(JSON.stringify(entry));
    }

    const res = await fetch(`${API_BASE_URL}/api/v1/observatory/entries/${entryId}`);
    if (res.status === 404) {
      throw new Error(`Publicación no encontrada o sin análisis vigente (ID: ${entryId})`);
    }
    if (!res.ok) {
      throw new Error(`Error al obtener detalle de publicación: HTTP ${res.status}`);
    }
    return res.json();
  }
}

export const observatoryApi = new ObservatoryApiService();
