export interface TaxonomyTopicItem {
  id: string;
  code: string;
  name: string;
  description: string | null;
  parent_id: string | null;
  parent_code: string | null;
  active: boolean;
  priority: number;
}

export interface TaxonomyAreaNode {
  id: string;
  code: string;
  name: string;
  description: string | null;
  active: boolean;
  priority: number;
  children: TaxonomyTopicItem[];
}

export interface TaxonomyMatrixResponse {
  matrix_id: string;
  code: string;
  name: string;
  status: string;
  updated_at: string;
  areas: TaxonomyAreaNode[];
}

export interface CreateAreaPayload {
  base_matrix_id: string;
  name: string;
  description?: string;
}

export interface UpdateAreaPayload {
  base_matrix_id: string;
  name?: string;
  description?: string;
}

export interface CreateTopicPayload {
  base_matrix_id: string;
  area_id: string;
  name: string;
  description?: string;
}

export interface UpdateTopicPayload {
  base_matrix_id: string;
  name?: string;
  description?: string;
  area_id?: string;
}

export interface ToggleStatusPayload {
  base_matrix_id: string;
}
