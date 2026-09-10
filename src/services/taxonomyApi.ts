import {
  TaxonomyMatrixResponse,
  CreateAreaPayload,
  UpdateAreaPayload,
  CreateTopicPayload,
  UpdateTopicPayload,
  ToggleStatusPayload,
} from '../types/taxonomy';
import { observatoryApi } from './observatoryApi';

const DEV_MOCK_TAXONOMY: TaxonomyMatrixResponse = {
  matrix_id: 'mock-matrix-v01',
  code: 'HITCHINGS-v0.1',
  name: 'Matriz de Seguimiento HITCHINGS',
  status: 'active',
  updated_at: '2026-09-10T12:00:00Z',
  areas: [
    {
      id: 'mock-area-1',
      code: 'competition_law_general',
      name: 'Derecho de la competencia – General',
      description: 'Regulación general y administrativa de la competencia',
      active: true,
      priority: 1,
      children: [
        {
          id: 'mock-topic-101',
          code: 'antitrust_general',
          name: 'Competencia / antitrust general',
          description: null,
          parent_id: 'mock-area-1',
          parent_code: 'competition_law_general',
          active: true,
          priority: 1,
        },
        {
          id: 'mock-topic-102',
          code: 'cartels_agreements',
          name: 'Cárteles y acuerdos anticompetitivos',
          description: null,
          parent_id: 'mock-area-1',
          parent_code: 'competition_law_general',
          active: true,
          priority: 2,
        },
        {
          id: 'mock-topic-103',
          code: 'abuse_dominance',
          name: 'Abuso de posición dominante',
          description: null,
          parent_id: 'mock-area-1',
          parent_code: 'competition_law_general',
          active: true,
          priority: 3,
        },
        {
          id: 'mock-topic-104',
          code: 'merger_control',
          name: 'Control de concentraciones',
          description: null,
          parent_id: 'mock-area-1',
          parent_code: 'competition_law_general',
          active: true,
          priority: 4,
        },
      ],
    },
    {
      id: 'mock-area-2',
      code: 'private_enforcement',
      name: 'Aplicación privada',
      description: 'Litigación y reclamaciones privadas de daños',
      active: true,
      priority: 2,
      children: [
        {
          id: 'mock-topic-201',
          code: 'damages_actions',
          name: 'Acciones de daños',
          description: null,
          parent_id: 'mock-area-2',
          parent_code: 'private_enforcement',
          active: true,
          priority: 1,
        },
        {
          id: 'mock-topic-202',
          code: 'collective_actions',
          name: 'Acciones colectivas',
          description: null,
          parent_id: 'mock-area-2',
          parent_code: 'private_enforcement',
          active: true,
          priority: 2,
        },
        {
          id: 'mock-topic-203',
          code: 'litigation_funding',
          name: 'Financiación de litigios',
          description: null,
          parent_id: 'mock-area-2',
          parent_code: 'private_enforcement',
          active: true,
          priority: 3,
        },
      ],
    },
  ],
};

export class TaxonomyApiService {
  private baseUrl: string;
  private mockTaxonomy: TaxonomyMatrixResponse = JSON.parse(JSON.stringify(DEV_MOCK_TAXONOMY));

  constructor(customBaseUrl?: string) {
    const isProd = typeof import.meta !== 'undefined' && import.meta.env?.PROD === true;
    const envBaseUrl =
      typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL !== undefined
        ? import.meta.env.VITE_API_BASE_URL
        : undefined;

    let rawBaseUrl: string;
    if (customBaseUrl !== undefined) {
      rawBaseUrl = customBaseUrl;
    } else if (envBaseUrl !== undefined) {
      rawBaseUrl = envBaseUrl;
    } else if (isProd) {
      rawBaseUrl = '';
    } else {
      rawBaseUrl = 'http://127.0.0.1:8000';
    }
    this.baseUrl = rawBaseUrl.trim().replace(/\/$/, '');
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    if (observatoryApi.isUsingMock()) {
      return this.handleMockRequest<T>(endpoint, options);
    }

    const url = `${this.baseUrl}${endpoint}`;
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...options.headers,
    };

    try {
      const response = await fetch(url, {
        ...options,
        headers,
        credentials: 'include',
      });

      if (!response.ok) {
        let errDetail = `Error ${response.status}: ${response.statusText}`;
        try {
          const errData = await response.json();
          if (errData?.detail) {
            errDetail = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail);
          }
        } catch {
          // ignore json parse error
        }
        const error = new Error(errDetail) as Error & { status?: number };
        error.status = response.status;
        throw error;
      }

      return (await response.json()) as T;
    } catch (err: unknown) {
      const errorObj = err as { status?: number; message?: string };
      if (errorObj.status === 401 || errorObj.status === 403 || errorObj.status === 409) {
        throw err;
      }

      if (import.meta.env?.DEV) {
        console.warn(`[TaxonomyApi] Request failed to ${url}, falling back to mock:`, err);
        return this.handleMockRequest<T>(endpoint, options);
      }
      throw err;
    }
  }

  private handleMockRequest<T>(endpoint: string, options: RequestInit): T {
    const method = options.method?.toUpperCase() || 'GET';

    if (endpoint === '/api/v1/taxonomy' && method === 'GET') {
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    const bumpMockVersion = () => {
      const curr = this.mockTaxonomy.code;
      const num = parseInt(curr.split('.').pop() || '1', 10) + 1;
      this.mockTaxonomy.code = `HITCHINGS-v0.${num}`;
      this.mockTaxonomy.matrix_id = `mock-matrix-v0${num}`;
      this.mockTaxonomy.updated_at = new Date().toISOString();
    };

    if (endpoint === '/api/v1/taxonomy/areas' && method === 'POST') {
      const payload: CreateAreaPayload = JSON.parse(options.body as string);
      if (payload.base_matrix_id !== this.mockTaxonomy.matrix_id) {
        const err = new Error('La configuración ha sido modificada por otro usuario. Recargue los datos.') as Error & { status?: number };
        err.status = 409;
        throw err;
      }
      bumpMockVersion();
      this.mockTaxonomy.areas.push({
        id: `mock-area-${Date.now()}`,
        code: payload.name.toLowerCase().replace(/\s+/g, '_'),
        name: payload.name,
        description: payload.description || null,
        active: true,
        priority: this.mockTaxonomy.areas.length + 1,
        children: [],
      });
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint.startsWith('/api/v1/taxonomy/areas/') && endpoint.endsWith('/archive') && method === 'POST') {
      const areaId = endpoint.split('/')[4];
      bumpMockVersion();
      const area = this.mockTaxonomy.areas.find(a => a.id === areaId);
      if (area) {
        area.active = false;
        area.children.forEach(c => (c.active = false));
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint.startsWith('/api/v1/taxonomy/areas/') && endpoint.endsWith('/reactivate') && method === 'POST') {
      const areaId = endpoint.split('/')[4];
      bumpMockVersion();
      const area = this.mockTaxonomy.areas.find(a => a.id === areaId);
      if (area) {
        area.active = true;
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint.startsWith('/api/v1/taxonomy/areas/') && method === 'PATCH') {
      const areaId = endpoint.split('/')[4];
      const payload: UpdateAreaPayload = JSON.parse(options.body as string);
      bumpMockVersion();
      const area = this.mockTaxonomy.areas.find(a => a.id === areaId);
      if (area) {
        if (payload.name) area.name = payload.name;
        if (payload.description !== undefined) area.description = payload.description || null;
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint === '/api/v1/taxonomy/topics' && method === 'POST') {
      const payload: CreateTopicPayload = JSON.parse(options.body as string);
      bumpMockVersion();
      const area = this.mockTaxonomy.areas.find(a => a.id === payload.area_id);
      if (area) {
        area.children.push({
          id: `mock-topic-${Date.now()}`,
          code: payload.name.toLowerCase().replace(/\s+/g, '_'),
          name: payload.name,
          description: payload.description || null,
          parent_id: area.id,
          parent_code: area.code,
          active: true,
          priority: area.children.length + 1,
        });
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint.startsWith('/api/v1/taxonomy/topics/') && endpoint.endsWith('/archive') && method === 'POST') {
      const topicId = endpoint.split('/')[4];
      bumpMockVersion();
      for (const area of this.mockTaxonomy.areas) {
        const topic = area.children.find(t => t.id === topicId);
        if (topic) {
          topic.active = false;
          break;
        }
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint.startsWith('/api/v1/taxonomy/topics/') && endpoint.endsWith('/reactivate') && method === 'POST') {
      const topicId = endpoint.split('/')[4];
      bumpMockVersion();
      for (const area of this.mockTaxonomy.areas) {
        const topic = area.children.find(t => t.id === topicId);
        if (topic) {
          topic.active = true;
          break;
        }
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    if (endpoint.startsWith('/api/v1/taxonomy/topics/') && method === 'PATCH') {
      const topicId = endpoint.split('/')[4];
      const payload: UpdateTopicPayload = JSON.parse(options.body as string);
      bumpMockVersion();
      let foundTopic: any = null;
      let oldArea: any = null;
      for (const a of this.mockTaxonomy.areas) {
        const idx = a.children.findIndex(t => t.id === topicId);
        if (idx !== -1) {
          foundTopic = a.children[idx];
          oldArea = a;
          if (payload.area_id && payload.area_id !== a.id) {
            a.children.splice(idx, 1);
          }
          break;
        }
      }
      if (foundTopic) {
        if (payload.name) foundTopic.name = payload.name;
        if (payload.description !== undefined) foundTopic.description = payload.description || null;
        if (payload.area_id && payload.area_id !== oldArea.id) {
          const newArea = this.mockTaxonomy.areas.find(a => a.id === payload.area_id);
          if (newArea) {
            foundTopic.parent_id = newArea.id;
            foundTopic.parent_code = newArea.code;
            newArea.children.push(foundTopic);
          }
        }
      }
      return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
    }

    return JSON.parse(JSON.stringify(this.mockTaxonomy)) as T;
  }

  public async getTaxonomy(): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>('/api/v1/taxonomy');
  }

  public async createArea(payload: CreateAreaPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>('/api/v1/taxonomy/areas', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async updateArea(areaId: string, payload: UpdateAreaPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>(`/api/v1/taxonomy/areas/${areaId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  public async archiveArea(areaId: string, payload: ToggleStatusPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>(`/api/v1/taxonomy/areas/${areaId}/archive`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async reactivateArea(areaId: string, payload: ToggleStatusPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>(`/api/v1/taxonomy/areas/${areaId}/reactivate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async createTopic(payload: CreateTopicPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>('/api/v1/taxonomy/topics', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async updateTopic(topicId: string, payload: UpdateTopicPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>(`/api/v1/taxonomy/topics/${topicId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  public async archiveTopic(topicId: string, payload: ToggleStatusPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>(`/api/v1/taxonomy/topics/${topicId}/archive`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async reactivateTopic(topicId: string, payload: ToggleStatusPayload): Promise<TaxonomyMatrixResponse> {
    return this.request<TaxonomyMatrixResponse>(`/api/v1/taxonomy/topics/${topicId}/reactivate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }
}

export const taxonomyApi = new TaxonomyApiService();
