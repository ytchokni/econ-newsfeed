export interface Author {
  id: number;
  first_name: string;
  last_name: string;
}

export type PublicationStatus =
  | "published"
  | "accepted"
  | "revise_and_resubmit"
  | "reject_and_resubmit"
  | "working_paper";

export type DraftUrlStatus = "unchecked" | "valid" | "invalid" | "timeout";

export type LinkType =
  | "pdf" | "ssrn" | "nber" | "arxiv" | "doi"
  | "journal" | "drive" | "dropbox" | "repository" | "other";

export interface PaperLink {
  url: string;
  link_type: LinkType | null;
}

export type EventType = 'new_paper' | 'status_change';

export interface OpenAlexCoAuthor {
  display_name: string;
  openalex_author_id: string | null;
}

export interface Publication {
  id: number;
  title: string;
  authors: Author[];
  year: string | null;
  venue: string | null;
  source_url: string | null;
  discovered_at: string;
  status: PublicationStatus | null;
  abstract: string | null;
  draft_url: string | null;
  draft_url_status: DraftUrlStatus;
  draft_available: boolean;
  doi: string | null;
  coauthors: OpenAlexCoAuthor[];
  links: PaperLink[];
  event_id?: number;
  event_type?: EventType;
  old_status?: PublicationStatus | null;
  new_status?: PublicationStatus | null;
  event_date?: string;
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

export interface JelCode {
  code: string;
  name: string;
}

export interface Researcher {
  id: number;
  first_name: string;
  last_name: string;
  position: string | null;
  affiliation: string | null;
  description: string | null;
  urls: ResearcherUrl[];
  website_url: string | null;
  publication_count: number;
  fields: ResearchField[];
  jel_codes: JelCode[];
}

export interface FeedFilters {
  status?: string;
  institution?: string;
  preset?: string;
  year?: string;
  search?: string;
}

export interface ResearcherDetail extends Researcher {
  publications: Publication[];
}

export interface FilterOptions {
  institutions: string[];
  positions: string[];
  fields: ResearchField[];
}

export interface ResearcherFilters {
  institution?: string;
  field?: string;
  position?: string;
  search?: string;
}
