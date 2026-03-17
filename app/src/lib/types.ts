export interface Author {
  id: number;
  first_name: string;
  last_name: string;
}

export type PublicationStatus =
  | "published"
  | "accepted"
  | "revise_and_resubmit"
  | "reject_and_resubmit";

export interface Publication {
  id: number;
  title: string;
  authors: Author[];
  year: string | null;
  venue: string | null;
  source_url: string | null;
  discovered_at: string;
  status: PublicationStatus | null;
  draft_url: string | null;
  draft_available: boolean;
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

export interface ResearchField {
  id: number;
  name: string;
  slug: string;
}

export interface Researcher {
  id: number;
  first_name: string;
  last_name: string;
  position: string | null;
  affiliation: string | null;
  urls: ResearcherUrl[];
  website_url: string | null;
  publication_count: number;
  fields: ResearchField[];
}

export interface ResearcherDetail extends Researcher {
  publications: Publication[];
}
