import { TrackingTopic, UpdateTopicKeywordsPayload } from '../types/tracking';
import { observatoryApi } from './observatoryApi';

const DEV_MOCK_TOPICS: TrackingTopic[] = [
  {
    id: 'mock-topic-private-enforcement',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'private_enforcement',
    name: 'Aplicación privada',
    description: 'Acciones de daños e indemnizaciones derivadas de infracciones del Derecho de la competencia.',
    relevance_instructions: 'Acciones civiles de reclamación de daños ante tribunales mercantiles y civiles.',
    keywords: [
      'private enforcement',
      'competition damages',
      'antitrust damages',
      'aplicación privada',
      'daños competencia',
      'acciones por daños por infracciones del Derecho de la competencia',
    ],
    discovery_queries: ['competition damages actions', 'antitrust private litigation'],
    priority: 100,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-cartels',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'cartels',
    name: 'Cárteles y acuerdos colusorios',
    description: 'Acuerdos horizontales, cárteles secretos, fijación de precios y reparto de mercado.',
    relevance_instructions: 'Infracciones del artículo 101 TFUE y artículo 1 de la Ley 15/2007.',
    keywords: [
      'cartel',
      'cartels',
      'cártel',
      'cárteles',
      'price fixing',
      'bid rigging',
      'reparto de mercado',
      '101 TFEU',
      'Article 101',
    ],
    discovery_queries: ['cartel enforcement CNMC', 'cartels European Commission'],
    priority: 90,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-abuse-dominance',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'abuse_dominance',
    name: 'Abuso de posición de dominio',
    description: 'Prácticas exclusionarias o explotativas por empresas en posición dominante.',
    relevance_instructions: 'Infracciones del artículo 102 TFUE y artículo 2 de la Ley 15/2007.',
    keywords: [
      'abuse of dominance',
      'abuso de posición dominante',
      'dominant position',
      '102 TFEU',
      'Article 102',
      'tying',
      'predatory pricing',
      'refusal to supply',
    ],
    discovery_queries: ['abuse of dominant position 102 TFEU'],
    priority: 85,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-unfair-competition',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'unfair_competition',
    name: 'Competencia desleal',
    description: 'Actos de competencia desleal con afectación al interés público y prevalimiento.',
    relevance_instructions: 'Ley 3/1991 (LCD) y artículo 3 de la Ley 15/2007 (LDC).',
    keywords: [
      'competencia desleal',
      'unfair competition',
      'Ley 3/1991',
      'LCD',
      'artículo 3 LDC',
      'public interest competition',
      'false advertising',
    ],
    discovery_queries: ['competencia desleal tribunal supremo', 'artículo 3 ley defensa competencia'],
    priority: 75,
    active: true,
    provisional: false,
    created_at: '2026-10-14T12:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-ai-ip',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'ai_intellectual_property',
    name: 'IA y Propiedad Intelectual',
    description: 'Intersección entre inteligencia artificial, derechos de autor y regulación competitiva.',
    relevance_instructions: 'Modelos generativos, derechos de propiedad intelectual y competencia digital.',
    keywords: [
      'inteligencia artificial',
      'artificial intelligence',
      'IA',
      'AI',
      'propiedad intelectual',
      'copyright',
      'derechos de autor',
      'IP antitrust',
    ],
    discovery_queries: ['inteligencia artificial competencia', 'artificial intelligence copyright antitrust'],
    priority: 70,
    active: true,
    provisional: false,
    created_at: '2026-10-14T12:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-dma',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'dma_enforcement',
    name: 'Aplicación del Reglamento de Mercados Digitales (DMA)',
    description: 'Obligaciones y enforcement del Reglamento (UE) 2022/1925 sobre gatekeepers.',
    relevance_instructions: 'Digital Markets Act, gatekeepers, interoperabilidad y auto-preferencia.',
    keywords: [
      'Digital Markets Act',
      'DMA',
      'Regulation (EU) 2022/1925',
      'Reglamento 2022/1925',
      'gatekeepers',
      'guardianes de acceso',
    ],
    discovery_queries: ['Digital Markets Act DMA gatekeepers compliance'],
    priority: 80,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-collective-redress',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'collective_redress',
    name: 'Acciones colectivas y representativas',
    description: 'Mecanismos de tutela colectiva en litigación de daños por competencia.',
    relevance_instructions: 'Directiva (UE) 2020/1828 y acciones de representación.',
    keywords: [
      'collective redress',
      'class actions',
      'acciones colectivas',
      'acción colectiva',
      'tutela colectiva',
      'acciones de representación',
      'representative actions',
      'Directive 2020/1828',
      'Directiva 2020/1828',
    ],
    discovery_queries: ['collective redress competition Directive 2020/1828'],
    priority: 85,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
  {
    id: 'mock-topic-procedural',
    matrix_id: 'mock-matrix-v01',
    parent_id: null,
    code: 'procedural_issues',
    name: 'Cuestiones procesales y prescripción',
    description: 'Plazos de prescripción, legitimación activa y acceso a fuentes de prueba.',
    relevance_instructions: 'Directiva 2014/104/UE y Ley de Enjuiciamiento Civil.',
    keywords: [
      'Directiva 2014/104',
      'Directive 2014/104',
      'prescripción',
      'limitation period',
      'disclosure',
      'acceso a fuentes de prueba',
      'legitimación',
    ],
    discovery_queries: ['Directiva 2014/104 prescripción daños'],
    priority: 90,
    active: true,
    provisional: false,
    created_at: '2026-09-07T10:00:00Z',
    updated_at: '2026-10-14T12:00:00Z',
  },
];

export class TrackingApiService {
  private baseUrl: string;
  private mockTopics: TrackingTopic[];

  constructor() {
    let rawBaseUrl = import.meta.env.VITE_API_BASE_URL || '';
    if (!rawBaseUrl && typeof window !== 'undefined') {
      rawBaseUrl = window.location.origin;
    }
    if (!rawBaseUrl) {
      rawBaseUrl = 'http://127.0.0.1:8000';
    }
    this.baseUrl = rawBaseUrl.trim().replace(/\/$/, '');
    this.mockTopics = JSON.parse(JSON.stringify(DEV_MOCK_TOPICS));
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
        console.warn(`[TrackingApi] Request failed to ${url}, falling back to mock:`, err);
        return this.handleMockRequest<T>(endpoint, options);
      }
      throw err;
    }
  }

  private handleMockRequest<T>(endpoint: string, options: RequestInit): T {
    const method = options.method?.toUpperCase() || 'GET';

    // GET /api/v1/tracking/topics
    if (endpoint.startsWith('/api/v1/tracking/topics') && method === 'GET') {
      const parts = endpoint.split('?')[0].split('/');
      // If single topic requested
      if (parts.length > 5 && parts[4]) {
        const topicId = parts[4];
        const topic = this.mockTopics.find((t) => t.id === topicId);
        if (!topic) {
          const err = new Error('Tracking topic not found') as Error & { status?: number };
          err.status = 404;
          throw err;
        }
        return JSON.parse(JSON.stringify(topic)) as T;
      }
      return JSON.parse(JSON.stringify(this.mockTopics)) as T;
    }

    // PATCH /api/v1/tracking/topics/{topic_id}
    if (endpoint.startsWith('/api/v1/tracking/topics/') && method === 'PATCH') {
      const topicId = endpoint.split('/')[4];
      const payload: UpdateTopicKeywordsPayload = JSON.parse(options.body as string);
      const topic = this.mockTopics.find((t) => t.id === topicId);
      if (!topic) {
        const err = new Error('Tracking topic not found') as Error & { status?: number };
        err.status = 404;
        throw err;
      }
      if (payload.keywords !== undefined) {
        topic.keywords = payload.keywords;
        topic.updated_at = new Date().toISOString();
      }
      return JSON.parse(JSON.stringify(topic)) as T;
    }

    return JSON.parse(JSON.stringify(this.mockTopics)) as T;
  }

  public async getTopics(activeOnly: boolean = false): Promise<TrackingTopic[]> {
    const query = activeOnly ? '?active=true' : '';
    return this.request<TrackingTopic[]>(`/api/v1/tracking/topics${query}`);
  }

  public async getTopic(topicId: string): Promise<TrackingTopic> {
    return this.request<TrackingTopic>(`/api/v1/tracking/topics/${topicId}`);
  }

  public async updateTopicKeywords(topicId: string, keywords: string[]): Promise<TrackingTopic> {
    const payload: UpdateTopicKeywordsPayload = { keywords };
    return this.request<TrackingTopic>(`/api/v1/tracking/topics/${topicId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }
}

export const trackingApi = new TrackingApiService();
