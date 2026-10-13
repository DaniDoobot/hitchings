import {
  DocumentDetail,
  DocumentDuplicateResponse,
  DocumentIngestionResponse,
  DocumentListItem,
  DocumentListResponse,
  DocumentsQueryParams,
  IngestUrlRequest,
  UploadDocumentParams,
} from '../types/document';
import { MOCK_ENTRIES } from '../data/mockObservatoryData';

export class ApiError extends Error {
  public status: number;
  public detail?: string;
  public duplicateData?: DocumentDuplicateResponse;

  constructor(
    message: string,
    status: number,
    detail?: string,
    duplicateData?: DocumentDuplicateResponse
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.duplicateData = duplicateData;
  }
}

export interface DocumentsApiOptions {
  apiBaseUrl?: string;
  useMock?: boolean;
  isProd?: boolean;
}

export class DocumentsApiService {
  private apiBaseUrl: string = '';
  private isMockMode: boolean = false;

  constructor(options?: DocumentsApiOptions) {
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

    if (isProd && useMock) {
      throw new Error('Security Error: Mock data is strictly disabled in production environments.');
    } else if (useMock) {
      this.isMockMode = true;
    } else {
      this.apiBaseUrl = baseUrl;
    }
  }

  public isUsingMock(): boolean {
    return this.isMockMode;
  }

  public buildQueryParams(params: DocumentsQueryParams = {}): URLSearchParams {
    const query = new URLSearchParams();
    if (params.limit !== undefined) query.set('limit', String(params.limit));
    if (params.offset !== undefined) query.set('offset', String(params.offset));
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.sort_order) query.set('sort_order', params.sort_order);
    if (params.q) query.set('q', params.q);
    if (params.date_from) query.set('date_from', params.date_from);
    if (params.date_to) query.set('date_to', params.date_to);
    if (params.source_id) query.set('source_id', params.source_id);
    if (params.origin_category && params.origin_category !== 'all') {
      query.set('origin_category', params.origin_category);
    }
    if (params.has_analysis && params.has_analysis !== 'all') {
      query.set('has_analysis', params.has_analysis);
    }
    if (params.relevance_status) {
      query.set('relevance_status', params.relevance_status);
    }
    return query;
  }

  public async getDocuments(params: DocumentsQueryParams = {}): Promise<DocumentListResponse> {
    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 100));
      // Convert mock entries to document items
      const mockItems: DocumentListItem[] = MOCK_ENTRIES.map((entry, index) => ({
        id: entry.entry_id,
        title: entry.title,
        url: entry.url,
        canonical_url: entry.url,
        author: entry.author,
        published_at: entry.published_at,
        captured_at: entry.published_at || new Date().toISOString(),
        content_type: entry.content_type,
        source: {
          id: entry.source.id,
          name: entry.source.name,
          type: entry.source.type,
          category: entry.source.category,
        },
        is_linkedin: Boolean(entry.is_linkedin),
        source_origin_category: entry.source_origin_category || 'other',
        excerpt: entry.summary,
        has_analysis: index % 4 !== 3, // Simulate occasional unanalysed item
        analysis:
          index % 4 !== 3
            ? {
                id: entry.entry_id,
                status: 'completed',
                relevance_status: entry.relevance.status,
                relevance_score: entry.relevance.score,
                confidence: entry.relevance.confidence,
                summary: entry.summary,
                key_points: entry.key_points,
                canonical_topics: entry.canonical_topics,
                canonical_primary_topic: entry.canonical_primary_topic,
                analyzed_at: entry.published_at,
              }
            : null,
      }));

      let filtered = mockItems;
      if (params.q) {
        const qLower = params.q.toLowerCase();
        filtered = filtered.filter(
          (item) =>
            (item.title && item.title.toLowerCase().includes(qLower)) ||
            (item.author && item.author.toLowerCase().includes(qLower)) ||
            (item.excerpt && item.excerpt.toLowerCase().includes(qLower))
        );
      }
      if (params.has_analysis === 'with_analysis') {
        filtered = filtered.filter((item) => item.has_analysis);
      } else if (params.has_analysis === 'without_analysis') {
        filtered = filtered.filter((item) => !item.has_analysis);
      }
      if (params.source_id) {
        filtered = filtered.filter((item) => item.source.id === params.source_id);
      }

      const total = filtered.length;
      const limit = params.limit || 20;
      const offset = params.offset || 0;
      return {
        items: filtered.slice(offset, offset + limit),
        total,
        limit,
        offset,
      };
    }

    const query = this.buildQueryParams(params);
    const res = await fetch(`${this.apiBaseUrl}/api/v1/documents?${query.toString()}`, {
      credentials: 'include',
    });
    if (res.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('hitchings:unauthorized'));
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Error al consultar fondo documental: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async getDocument(documentId: string): Promise<DocumentDetail> {
    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 80));
      const entry = MOCK_ENTRIES.find((e) => e.entry_id === documentId);
      if (!entry) {
        throw new Error(`Documento no encontrado (ID: ${documentId})`);
      }
      return {
        id: entry.entry_id,
        title: entry.title,
        url: entry.url,
        canonical_url: entry.url,
        author: entry.author,
        published_at: entry.published_at,
        captured_at: entry.published_at || new Date().toISOString(),
        content_type: entry.content_type,
        source: {
          id: entry.source.id,
          name: entry.source.name,
          type: entry.source.type,
          category: entry.source.category,
        },
        is_linkedin: Boolean(entry.is_linkedin),
        source_origin_category: entry.source_origin_category || 'other',
        content: `Contenido documental íntegro de la publicación ${entry.title || ''}.\n\nSección I: Antecedentes de hecho y objeto de la resolución judicial.\nSección II: Fundamentos jurídicos de defensa de la competencia.`,
        excerpt: entry.summary,
        has_analysis: true,
        analysis: {
          id: entry.entry_id,
          status: 'completed',
          relevance_status: entry.relevance.status,
          relevance_score: entry.relevance.score,
          confidence: entry.relevance.confidence,
          summary: entry.summary,
          key_points: entry.key_points,
          canonical_topics: entry.canonical_topics,
          canonical_primary_topic: entry.canonical_primary_topic,
          analyzed_at: entry.published_at,
        },
        evidence: {
          source: 'deep',
          summary_quotes: [{ source_field: 'content', quote: 'Antecedentes de hecho y objeto de la resolución judicial.' }],
          key_points: [],
        },
      };
    }

    const res = await fetch(`${this.apiBaseUrl}/api/v1/documents/${documentId}`, {
      credentials: 'include',
    });
    if (res.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('hitchings:unauthorized'));
    }
    if (res.status === 404) {
      throw new Error(`Documento no encontrado (ID: ${documentId})`);
    }
    if (!res.ok) {
      throw new Error(`Error al obtener documento: HTTP ${res.status}`);
    }
    return res.json();
  }

  public async ingestDocumentFromUrl(
    params: IngestUrlRequest
  ): Promise<DocumentIngestionResponse | DocumentDuplicateResponse> {
    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 150));
      return {
        status: 'created',
        entry_id: 'mock-url-' + Date.now(),
        title: params.manual_title || 'Documento desde URL',
        url: params.url,
        canonical_url: params.url,
        content_type: 'document',
        analysis_requested: Boolean(params.analyze_with_ai),
        analysis_scheduled: Boolean(params.analyze_with_ai),
        created_at: new Date().toISOString(),
      };
    }

    const res = await fetch(`${this.apiBaseUrl}/api/v1/documents/url`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(params),
    });

    if (res.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('hitchings:unauthorized'));
    }

    if (res.status === 409) {
      const dupData = (await res.json().catch(() => ({}))) as DocumentDuplicateResponse;
      return dupData;
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(
        err.detail || `Error al procesar URL: HTTP ${res.status}`,
        res.status,
        err.detail
      );
    }

    return (await res.json()) as DocumentIngestionResponse;
  }

  public async uploadDocument(
    params: UploadDocumentParams
  ): Promise<DocumentIngestionResponse | DocumentDuplicateResponse> {
    if (this.isMockMode) {
      await new Promise((r) => setTimeout(r, 150));
      return {
        status: 'created',
        entry_id: 'mock-upload-' + Date.now(),
        title: params.manual_title || params.file.name,
        url: `upload://manual/mock/${params.file.name}`,
        canonical_url: `upload://manual/mock/${params.file.name}`,
        content_type: params.file.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'document',
        analysis_requested: Boolean(params.analyze_with_ai),
        analysis_scheduled: Boolean(params.analyze_with_ai),
        created_at: new Date().toISOString(),
      };
    }

    const formData = new FormData();
    formData.append('file', params.file);
    if (params.analyze_with_ai !== undefined) {
      formData.append('analyze_with_ai', String(params.analyze_with_ai));
    }
    if (params.manual_title) {
      formData.append('manual_title', params.manual_title);
    }
    if (params.manual_author) {
      formData.append('manual_author', params.manual_author);
    }
    if (params.manual_published_at) {
      formData.append('manual_published_at', params.manual_published_at);
    }

    // Do NOT set Content-Type header so the browser automatically sets multipart/form-data boundary
    const res = await fetch(`${this.apiBaseUrl}/api/v1/documents/upload`, {
      method: 'POST',
      credentials: 'include',
      body: formData,
    });

    if (res.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('hitchings:unauthorized'));
    }

    if (res.status === 409) {
      const dupData = (await res.json().catch(() => ({}))) as DocumentDuplicateResponse;
      return dupData;
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(
        err.detail || `Error al subir documento: HTTP ${res.status}`,
        res.status,
        err.detail
      );
    }

    return (await res.json()) as DocumentIngestionResponse;
  }
}

export const documentsApi = new DocumentsApiService();
