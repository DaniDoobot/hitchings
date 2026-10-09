import {
  ObservatoryEvidence,
  ObservatoryTopicItem,
  RelevanceStatus,
} from './observatory';

export interface DocumentSourceRef {
  id: string;
  name: string;
  type?: string | null;
  category?: string | null;
  tracked_entity_name?: string | null;
}

export interface DocumentAnalysisSummary {
  id: string;
  status: string;
  relevance_status?: RelevanceStatus | null;
  relevance_score?: number | null;
  confidence?: number | null;
  summary?: string | null;
  key_points: string[];
  canonical_topics: ObservatoryTopicItem[];
  canonical_primary_topic?: ObservatoryTopicItem | null;
  analyzed_at?: string | null;
}

export interface DocumentListItem {
  id: string;
  title?: string | null;
  url: string;
  canonical_url?: string | null;
  author?: string | null;
  published_at?: string | null;
  captured_at: string;
  content_type?: string | null;
  language?: string | null;
  source: DocumentSourceRef;
  is_linkedin: boolean;
  source_origin_category: string;
  excerpt?: string | null;
  has_analysis: boolean;
  analysis?: DocumentAnalysisSummary | null;
}

export interface DocumentDetail {
  id: string;
  title?: string | null;
  url: string;
  canonical_url?: string | null;
  author?: string | null;
  published_at?: string | null;
  captured_at: string;
  content_type?: string | null;
  language?: string | null;
  source: DocumentSourceRef;
  is_linkedin: boolean;
  source_origin_category: string;
  content?: string | null;
  excerpt?: string | null;
  raw_metadata?: Record<string, unknown> | null;
  has_analysis: boolean;
  analysis?: DocumentAnalysisSummary | null;
  evidence?: ObservatoryEvidence | null;
}

export interface DocumentListResponse {
  items: DocumentListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentsQueryParams {
  limit?: number;
  offset?: number;
  sort_by?: 'published_at' | 'captured_at' | 'title';
  sort_order?: 'asc' | 'desc';
  q?: string;
  date_from?: string;
  date_to?: string;
  source_id?: string;
  origin_category?: 'all' | 'institutional' | 'linkedin' | 'expert_analysis';
  has_analysis?: 'all' | 'with_analysis' | 'without_analysis';
  relevance_status?: RelevanceStatus;
}
