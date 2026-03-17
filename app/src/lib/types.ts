export interface Author {
  id: number;
  first_name: string;
  last_name: string;
}

export interface Publication {
  id: number;
  title: string;
  authors: Author[];
  year: string | null;
  venue: string | null;
  source_url: string | null;
  discovered_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

export interface ResearcherUrl {
  id: number;
  page_type: string;
  url: string;
}

export interface Researcher {
  id: number;
  first_name: string;
  last_name: string;
  position: string | null;
  affiliation: string | null;
  urls: ResearcherUrl[];
  publication_count: number;
}

export interface ResearcherDetail extends Researcher {
  publications: Publication[];
}
