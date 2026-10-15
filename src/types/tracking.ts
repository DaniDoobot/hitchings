export interface TrackingTopic {
  id: string;
  matrix_id: string;
  parent_id?: string | null;
  code: string;
  name: string;
  description?: string | null;
  relevance_instructions?: string | null;
  keywords?: string[] | null;
  discovery_queries?: string[] | null;
  priority: number;
  active: boolean;
  provisional: boolean;
  created_at: string;
  updated_at: string;
}

export interface UpdateTopicKeywordsPayload {
  keywords: string[];
}
