export type Application = {
  id: string;
  title: string;
  company: string;
  status: string;
  isArchived: boolean;
  source: string;
  contactName: string | null;
  contactEmail: string | null;
  submittedAt: string;
  jobUrl: string;
};

export type ApplicationFilterInput = {
  statuses?: string[];
  q?: string;
  companies?: string[];
  sources?: string[];
  includeArchived?: boolean;
  hasContact?: boolean;
  discoveredFrom?: string;
  discoveredTo?: string;
  sortBy?: string;
  sortDir?: string;
};

export type FilterState = {
  statuses: string[];
  q: string;
  companiesText: string;
  sources: string[];
  includeArchived: boolean;
  hasContact: "all" | "yes" | "no";
  discoveredFrom: string;
  discoveredTo: string;
  sortBy: "discovered_at" | "company" | "status";
  sortDir: "asc" | "desc";
};

export const PAGE_SIZE = 25;
export const MAX_BULK_SELECTION = 10;

const SELECTABLE_STATUSES = new Set(["review", "viewed", "failed"]);

export const statusOptions = ["review", "viewed", "applying", "applied", "failed", "notified"];
export const sourceOptions = ["greenhouse", "lever", "smartrecruiters", "workday", "other"];

export function defaultFilters(): FilterState {
  return {
    statuses: [],
    q: "",
    companiesText: "",
    sources: [],
    includeArchived: false,
    hasContact: "all",
    discoveredFrom: "",
    discoveredTo: "",
    sortBy: "discovered_at",
    sortDir: "desc",
  };
}

export function formatSubmittedAt(dateString: string) {
  const parsed = new Date(dateString);
  if (Number.isNaN(parsed.getTime())) return "-";
  return parsed.toLocaleString();
}

export function normalizeStatus(status: string) {
  return status.trim().toLowerCase();
}

export function isSelectableStatus(status: string) {
  return SELECTABLE_STATUSES.has(normalizeStatus(status));
}
