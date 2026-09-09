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

export interface ObservatoryApiOptions {
  apiBaseUrl?: string;
  useMock?: boolean;
  isProd?: boolean;
  throwOnInit?: boolean;
}

export class ObservatoryApiService {
  private apiBaseUrl: string = '';
  private isMockMode: boolean = false;
  private configError: string | null = null;

  constructor(options?: ObservatoryApiOptions) {
    const isProd =
      options?.isProd !== undefined
        ? options.isProd
        : typeof import.meta !== 'undefined' && import.meta.env?.PROD === true;

    const useMock =
      options?.useMock !== undefined
        ? options.useMock
        : typeof import.meta !== 'undefined' && import.meta.env?.VITE_USE_MOCK_DATA === 'true';

    const rawBaseUrl =
      options?.apiBaseUrl !== undefined
        ? options.apiBaseUrl
        : (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) || '';

    const baseUrl = rawBaseUrl.trim().replace(/\/$/, '');

    // Rule 1: Production Protection against Mock
    if (isProd && useMock) {
      this.configError =
        'Security Error: Mock data is strictly disabled in production environments.';
    }
    // Rule 2: Explicit Mock Only in Development
    else if (useMock) {
      this.isMockMode = true;
      this.apiBaseUrl = '';
      if (typeof console !== 'undefined') {
        console.info('[ObservatoryApi] Running in explicit DEVELOPMENT MOCK mode.');
      }
    }
    // Rule 3: Fail-Closed when Mock is False and Base URL is Missing
    else if (!baseUrl) {
      this.configError = 'VITE_API_BASE_URL is required when mock data is disabled';
    }
    // Normal connected mode
    else {
      this.apiBaseUrl = baseUrl;
      this.isMockMode = false;
      if (typeof console !== 'undefined') {
        console.info(`[ObservatoryApi] Connected to backend API: ${this.apiBaseUrl}`);
      }
    }

    if (options?.throwOnInit && this.configError) {
      throw new Error(this.configError);
    }
  }

  private assertReady() {
    if (this.configError) {
      throw new Error(this.configError);
    }
  }

  public isUsingMock(): boolean {
    return this.isMockMode;
  }

  public getBaseUrl(): string {
    return this.apiBaseUrl;
  }

  public buildQueryParams(params: EntriesQueryParams = {}): URLSearchParams {
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
    return query;
  }

  public async getDashboard(): Promise<ObservatoryDashboard> {
    this.assertReady();

    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 100));
      return JSON.parse(JSON.stringify(MOCK_DASHBOARD));
    }

    const res = await fetch(`${this.apiBaseUrl}/api/v1/observatory/dashboard`);
    if (!res.ok) {
      throw new Error(`Error al obtener dashboard: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getSources(): Promise<ObservatorySourceDetail[]> {
    this.assertReady();

    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 80));
      return JSON.parse(JSON.stringify(MOCK_SOURCES));
    }

    const res = await fetch(`${this.apiBaseUrl}/api/v1/observatory/sources`);
    if (!res.ok) {
      throw new Error(`Error al obtener fuentes: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getTopics(): Promise<ObservatoryTopicNode[]> {
    this.assertReady();

    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 80));
      return JSON.parse(JSON.stringify(MOCK_TOPICS));
    }

    const res = await fetch(`${this.apiBaseUrl}/api/v1/observatory/topics`);
    if (!res.ok) {
      throw new Error(`Error al obtener taxonomía: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getEntries(params: EntriesQueryParams = {}): Promise<ObservatoryListResponse> {
    this.assertReady();

    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 120));
      return filterMockEntries(params);
    }

    const query = this.buildQueryParams(params);
    const res = await fetch(`${this.apiBaseUrl}/api/v1/observatory/entries?${query.toString()}`);
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || `Error al consultar publicaciones: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getEntry(entryId: string): Promise<ObservatoryEntryDetail> {
    this.assertReady();

    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 80));
      const entry = getMockEntryDetail(entryId);
      if (!entry) {
        throw new Error(`Publicación no encontrada o sin análisis vigente (ID: ${entryId})`);
      }
      return JSON.parse(JSON.stringify(entry));
    }

    const res = await fetch(`${this.apiBaseUrl}/api/v1/observatory/entries/${entryId}`);
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
