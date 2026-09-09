export type RelevanceStatus = 'relevant' | 'uncertain' | 'not_relevant';

export interface ObservatorySourceRef {
  id: string;
  name: string;
}

export interface ObservatoryTopicItem {
  code: string;
  name: string;
}

export interface ObservatoryTopicNode {
  code: string;
  name: string;
  parent_code: string | null;
  description?: string | null;
  children: ObservatoryTopicNode[];
}

export interface ObservatoryRelevance {
  status: RelevanceStatus;
  score: number;
  confidence: number;
}

export interface ObservatoryEvidenceQuote {
  source_field: string;
  quote: string;
}

export interface ObservatoryEvidencePoint {
  point: string;
  quotes: ObservatoryEvidenceQuote[];
}

export interface ObservatoryEvidence {
  source: string;
  summary_quotes: ObservatoryEvidenceQuote[];
  key_points: ObservatoryEvidencePoint[];
}

export interface ObservatoryEntryListItem {
  entry_id: string;
  title: string;
  source: ObservatorySourceRef;
  published_at: string;
  url: string;
  content_type?: string | null;
  relevance: ObservatoryRelevance;
  summary: string;
  canonical_topics: ObservatoryTopicItem[];
  canonical_primary_topic?: ObservatoryTopicItem | null;
  key_points: string[];
}

export interface ObservatoryEntryDetail {
  entry_id: string;
  title: string;
  source: ObservatorySourceRef;
  published_at: string;
  url: string;
  content_type?: string | null;
  relevance: ObservatoryRelevance;
  summary: string;
  canonical_topics: ObservatoryTopicItem[];
  canonical_primary_topic?: ObservatoryTopicItem | null;
  key_points: string[];
  evidence?: ObservatoryEvidence | null;
}

export interface ObservatoryListResponse {
  items: ObservatoryEntryListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ObservatorySourceDetail {
  id: string;
  name: string;
  type: string;
  url: string;
  entry_count: number;
  latest_published_at: string | null;
}

export interface DashboardTopTopic {
  code: string;
  name: string;
  count: number;
}

export interface DashboardTopSource {
  source_id: string;
  name: string;
  publication_count: number;
  relevant_count: number;
}

export interface DashboardLatestEntry {
  entry_id: string;
  title: string;
  source: ObservatorySourceRef;
  published_at: string;
  score: number;
  summary: string;
  canonical_topics: ObservatoryTopicItem[];
}

export interface ObservatoryDashboard {
  total_publications: number;
  relevant_count: number;
  uncertain_count: number;
  not_relevant_count: number;
  publications_last_7_days: number;
  publications_last_30_days: number;
  relevant_last_30_days: number;
  top_topics: DashboardTopTopic[];
  top_sources: DashboardTopSource[];
  latest_relevant_entries: DashboardLatestEntry[];
}

export interface EntriesQueryParams {
  limit?: number;
  offset?: number;
  sort_by?: 'published_at' | 'relevance_score';
  sort_order?: 'asc' | 'desc';
  q?: string;
  date_from?: string;
  date_to?: string;
  source_id?: string;
  source_ids?: string[];
  relevance_status?: RelevanceStatus;
  min_relevance_score?: number;
  topic_code?: string;
}
