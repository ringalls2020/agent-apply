"use client";

import { useMutation, useQuery } from "@apollo/client";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Button } from "@/components/ui/Button";
import { Card, CardDescription, CardTitle } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FormField } from "@/components/ui/FormField";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { StatusPill } from "@/components/ui/StatusPill";
import {
  APPLICATIONS_SEARCH,
  APPLY_SELECTED_APPLICATIONS,
  MARK_APPLICATION_APPLIED,
  MARK_APPLICATION_VIEWED,
  ME,
  RUN_AGENT,
} from "@/graphql/operations";
import { useRequireAuth } from "@/lib/useRequireAuth";

type Application = {
  id: string;
  title: string;
  company: string;
  status: string;
  source: string;
  contactName: string | null;
  contactEmail: string | null;
  submittedAt: string;
  jobUrl: string;
};

type ApplicationsSearchQuery = {
  applicationsSearch: {
    applications: Application[];
    totalCount: number;
    limit: number;
    offset: number;
  };
};

type UserProfile = {
  name: string;
  interests: string[];
  applicationsPerDay: number;
  autosubmitEnabled: boolean;
};

type MeQuery = {
  me: UserProfile | null;
};

type ApplicationFilterInput = {
  statuses?: string[];
  q?: string;
  companies?: string[];
  sources?: string[];
  hasContact?: boolean;
  discoveredFrom?: string;
  discoveredTo?: string;
  sortBy?: string;
  sortDir?: string;
};

type ApplySelectedApplicationsResponse = {
  applySelectedApplications: {
    runId: string | null;
    acceptedApplicationIds: string[];
    skipped: Array<{
      applicationId: string;
      reason: string;
      status: string | null;
    }>;
  };
};

const PAGE_SIZE = 25;
const MAX_BULK_SELECTION = 10;
const SELECTABLE_STATUSES = new Set(["review", "viewed"]);

const statusOptions = ["review", "viewed", "applying", "applied", "failed", "notified"];
const sourceOptions = ["greenhouse", "lever", "smartrecruiters", "workday", "other"];

type FilterState = {
  statuses: string[];
  q: string;
  companiesText: string;
  sources: string[];
  hasContact: "all" | "yes" | "no";
  discoveredFrom: string;
  discoveredTo: string;
  sortBy: "discovered_at" | "company" | "status";
  sortDir: "asc" | "desc";
};

function defaultFilters(): FilterState {
  return {
    statuses: [],
    q: "",
    companiesText: "",
    sources: [],
    hasContact: "all",
    discoveredFrom: "",
    discoveredTo: "",
    sortBy: "discovered_at",
    sortDir: "desc",
  };
}

function formatSubmittedAt(dateString: string) {
  const parsed = new Date(dateString);
  if (Number.isNaN(parsed.getTime())) return "-";
  return parsed.toLocaleString();
}

function normalizeStatus(status: string) {
  return status.trim().toLowerCase();
}

function isSelectableStatus(status: string) {
  return SELECTABLE_STATUSES.has(normalizeStatus(status));
}

function ApplicationsInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ variant: "success" | "error"; message: string } | null>(null);
  const [isPostRunRefreshing, setIsPostRunRefreshing] = useState(false);
  const [filters, setFilters] = useState<FilterState>(defaultFilters);
  const [offset, setOffset] = useState(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectionError, setSelectionError] = useState("");
  const [optimisticStatuses, setOptimisticStatuses] = useState<Record<string, string>>({});
  const [rowActionLoading, setRowActionLoading] = useState<Record<string, boolean>>({});
  const [defaultFiltersApplied, setDefaultFiltersApplied] = useState(false);

  const { data: meData } = useQuery<MeQuery>(ME, { skip: !isAuthenticated });

  useEffect(() => {
    if (!meData?.me || defaultFiltersApplied) return;
    if (!meData.me.autosubmitEnabled) {
      setFilters((current) => ({ ...current, statuses: ["review", "viewed"] }));
    }
    setDefaultFiltersApplied(true);
  }, [defaultFiltersApplied, meData]);

  const filterInput = useMemo<ApplicationFilterInput>(() => {
    const companies = filters.companiesText
      .split(",")
      .map((company) => company.trim())
      .filter(Boolean);

    const input: ApplicationFilterInput = {
      sortBy: filters.sortBy,
      sortDir: filters.sortDir,
    };

    if (filters.statuses.length) input.statuses = filters.statuses;
    if (filters.q.trim()) input.q = filters.q.trim();
    if (companies.length) input.companies = companies;
    if (filters.sources.length) input.sources = filters.sources;
    if (filters.hasContact === "yes") input.hasContact = true;
    if (filters.hasContact === "no") input.hasContact = false;
    if (filters.discoveredFrom) input.discoveredFrom = filters.discoveredFrom;
    if (filters.discoveredTo) input.discoveredTo = filters.discoveredTo;

    return input;
  }, [filters]);

  const {
    data,
    loading,
    refetch,
  } = useQuery<ApplicationsSearchQuery>(APPLICATIONS_SEARCH, {
    skip: !isAuthenticated,
    variables: {
      filter: filterInput,
      limit: PAGE_SIZE,
      offset,
    },
  });

  const [runAgent, { loading: running }] = useMutation(RUN_AGENT);
  const [applySelectedApplications, { loading: bulkApplying }] = useMutation<ApplySelectedApplicationsResponse>(
    APPLY_SELECTED_APPLICATIONS,
  );
  const [markApplicationViewed] = useMutation(MARK_APPLICATION_VIEWED);
  const [markApplicationApplied] = useMutation(MARK_APPLICATION_APPLIED);

  const apps = useMemo(
    () => data?.applicationsSearch.applications ?? [],
    [data?.applicationsSearch.applications],
  );
  const totalCount = data?.applicationsSearch.totalCount ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const profile = meData?.me;

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => apps.some((app) => app.id === id)));
  }, [apps]);

  const resetToFirstPage = () => {
    setOffset(0);
  };

  const updateFilters = (next: Partial<FilterState>) => {
    setFilters((current) => ({ ...current, ...next }));
    resetToFirstPage();
    setSelectedIds([]);
    setSelectionError("");
  };

  const toggleStatusFilter = (statusValue: string) => {
    const normalized = normalizeStatus(statusValue);
    setFilters((current) => {
      const exists = current.statuses.includes(normalized);
      return {
        ...current,
        statuses: exists
          ? current.statuses.filter((item) => item !== normalized)
          : [...current.statuses, normalized],
      };
    });
    resetToFirstPage();
    setSelectedIds([]);
  };

  const toggleSourceFilter = (sourceValue: string) => {
    const normalized = sourceValue.trim().toLowerCase();
    setFilters((current) => {
      const exists = current.sources.includes(normalized);
      return {
        ...current,
        sources: exists
          ? current.sources.filter((item) => item !== normalized)
          : [...current.sources, normalized],
      };
    });
    resetToFirstPage();
    setSelectedIds([]);
  };

  const getEffectiveStatus = (app: Application) => optimisticStatuses[app.id] ?? app.status;

  const toggleSelection = (applicationId: string, checked: boolean) => {
    setSelectionError("");
    if (!checked) {
      setSelectedIds((current) => current.filter((id) => id !== applicationId));
      return;
    }

    setSelectedIds((current) => {
      if (current.includes(applicationId)) return current;
      if (current.length >= MAX_BULK_SELECTION) {
        setSelectionError(`You can select up to ${MAX_BULK_SELECTION} applications per auto-apply batch.`);
        return current;
      }
      return [...current, applicationId];
    });
  };

  const selectAllEligibleOnPage = () => {
    setSelectionError("");
    const eligibleIds = apps
      .filter((app) => isSelectableStatus(getEffectiveStatus(app)))
      .map((app) => app.id);

    const allSelected = eligibleIds.every((id) => selectedIds.includes(id));
    if (allSelected) {
      setSelectedIds((current) => current.filter((id) => !eligibleIds.includes(id)));
      return;
    }

    setSelectedIds((current) => {
      const merged: string[] = [...current];
      for (const id of eligibleIds) {
        if (merged.includes(id)) continue;
        if (merged.length >= MAX_BULK_SELECTION) {
          setSelectionError(`Selection capped at ${MAX_BULK_SELECTION} applications.`);
          break;
        }
        merged.push(id);
      }
      return merged;
    });
  };

  const triggerRunAgent = async () => {
    setError("");
    setNotice(null);
    await runAgent();
    await refetch();

    if (meData?.me?.autosubmitEnabled) {
      setIsPostRunRefreshing(true);
      try {
        for (let attempt = 0; attempt < 5; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1200));
          await refetch();
        }
      } finally {
        setIsPostRunRefreshing(false);
      }
    }
  };

  const handleBulkApply = async () => {
    setError("");
    setNotice(null);
    setSelectionError("");

    if (!selectedIds.length) {
      setSelectionError("Select at least one review/viewed application first.");
      return;
    }

    try {
      const response = await applySelectedApplications({
        variables: { applicationIds: selectedIds },
      });
      const result = response.data?.applySelectedApplications;
      const acceptedCount = result?.acceptedApplicationIds.length ?? 0;
      const skippedCount = result?.skipped.length ?? 0;

      setSelectedIds([]);
      setOptimisticStatuses((current) => {
        const next = { ...current };
        for (const acceptedId of result?.acceptedApplicationIds ?? []) {
          next[acceptedId] = "applying";
        }
        return next;
      });

      setNotice({
        variant: "success",
        message: `Queued ${acceptedCount} application(s) for auto-apply${skippedCount ? `, skipped ${skippedCount}` : ""}.`,
      });

      await refetch();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not submit selected applications.");
    }
  };

  const handleMarkApplied = async (applicationId: string) => {
    setError("");
    setNotice(null);
    setRowActionLoading((current) => ({ ...current, [applicationId]: true }));
    try {
      await markApplicationApplied({ variables: { applicationId } });
      setOptimisticStatuses((current) => ({ ...current, [applicationId]: "applied" }));
      await refetch();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not mark application as applied.");
    } finally {
      setRowActionLoading((current) => ({ ...current, [applicationId]: false }));
    }
  };

  const clearFilters = () => {
    const base = defaultFilters();
    if (!profile?.autosubmitEnabled) {
      base.statuses = ["review", "viewed"];
    }
    setFilters(base);
    setOffset(0);
    setSelectedIds([]);
    setSelectionError("");
  };

  const columns: DataTableColumn<Application>[] = [
      {
        id: "select",
        header: "Select",
        mobileLabel: "Select",
        render: (app) => {
          const status = getEffectiveStatus(app);
          const selectable = isSelectableStatus(status);
          const checked = selectedIds.includes(app.id);
          return (
            <input
              aria-label={`Select ${app.title}`}
              type="checkbox"
              checked={checked}
              disabled={!selectable && !checked}
              onChange={(event) => toggleSelection(app.id, event.target.checked)}
              className="size-4 rounded border-border bg-surfaceAlt/70 text-accent focus-visible:ring-2 focus-visible:ring-accent/45"
            />
          );
        },
      },
      {
        id: "company",
        header: "Company",
        render: (app) => <span className="font-medium">{app.company}</span>,
      },
      {
        id: "role",
        header: "Role",
        render: (app) => (
          <a
            href={app.jobUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-accentSoft hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
            onClick={() => {
              const currentStatus = normalizeStatus(getEffectiveStatus(app));
              if (currentStatus !== "review") return;
              setOptimisticStatuses((current) => ({ ...current, [app.id]: "viewed" }));
              void markApplicationViewed({ variables: { applicationId: app.id } }).catch(() => undefined);
            }}
          >
            {app.title}
            <span aria-hidden="true" className="text-xs text-muted">
              {"->"}
            </span>
          </a>
        ),
      },
      {
        id: "source",
        header: "Source",
        render: (app) => <span className="text-muted capitalize">{app.source}</span>,
      },
      {
        id: "status",
        header: "Status",
        render: (app) => <StatusPill status={getEffectiveStatus(app)} />,
      },
      {
        id: "contact",
        header: "Point of contact",
        mobileLabel: "Contact",
        render: (app) => {
          if (!app.contactName && !app.contactEmail) {
            return <span className="text-muted">No contact found</span>;
          }

          return (
            <div className="space-y-0.5">
              <p className="font-medium text-foreground">{app.contactName ?? "Unknown contact"}</p>
              <p className="text-xs text-muted">{app.contactEmail ?? "No email"}</p>
            </div>
          );
        },
      },
      {
        id: "submitted",
        header: "Submitted",
        render: (app) => <span className="text-muted">{formatSubmittedAt(app.submittedAt)}</span>,
      },
      {
        id: "actions",
        header: "Actions",
        mobileLabel: "Actions",
        render: (app) => {
          const status = normalizeStatus(getEffectiveStatus(app));
          const canMarkApplied = status === "review" || status === "viewed";
          if (!canMarkApplied) {
            return <span className="text-xs text-muted">-</span>;
          }
          return (
            <Button
              variant="ghost"
              size="sm"
              loading={Boolean(rowActionLoading[app.id])}
              loadingText="Saving..."
              onClick={async () => {
                await handleMarkApplied(app.id);
              }}
            >
              Mark applied
            </Button>
          );
        },
      },
    ];

  if (isCheckingAuth) {
    return (
      <AppShell>
        <LoadingState label="Checking session..." />
      </AppShell>
    );
  }

  if (!isAuthenticated) {
    return (
      <AppShell>
        <LoadingState label="Redirecting to login..." />
      </AppShell>
    );
  }

  const canGoPrevious = offset > 0;
  const canGoNext = offset + PAGE_SIZE < totalCount;

  return (
    <AppShell className="pb-8">
      <Nav />

      <section className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Card variant="metric" className="space-y-1">
          <CardTitle>Applications</CardTitle>
          <p className="text-3xl font-semibold text-foreground">{totalCount}</p>
          <CardDescription>Total records matching current filters</CardDescription>
        </Card>

        <Card variant="metric" className="space-y-1">
          <CardTitle>Daily Rate</CardTitle>
          <p className="text-3xl font-semibold text-foreground">{profile?.applicationsPerDay ?? "-"}</p>
          <CardDescription>Configured automation velocity</CardDescription>
        </Card>

        <Card variant="metric" className="space-y-1 sm:col-span-2 xl:col-span-1">
          <CardTitle>Interests</CardTitle>
          <p className="text-sm text-foreground">{profile?.interests?.join(", ") || "No interest tags configured"}</p>
          <CardDescription>Active target domains for matching</CardDescription>
        </Card>
      </section>

      <Card className="mb-4 space-y-4" variant="elevated">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-foreground">Filters</h2>
            <p className="mt-1 text-sm text-muted">Narrow the review queue, then bulk auto-apply selected rows.</p>
          </div>
          <Button variant="secondary" size="sm" onClick={clearFilters}>
            Clear filters
          </Button>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <FormField
            id="applications-search"
            label="Keyword"
            placeholder="Role, company, contact..."
            value={filters.q}
            onChange={(event) => updateFilters({ q: event.target.value })}
          />

          <FormField
            id="applications-companies"
            label="Companies"
            placeholder="Comma-separated"
            value={filters.companiesText}
            onChange={(event) => updateFilters({ companiesText: event.target.value })}
          />

          <div className="space-y-2">
            <label htmlFor="applications-contact" className="block text-sm font-medium text-foreground">
              Contact
            </label>
            <select
              id="applications-contact"
              className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground focus:border-accent focus:ring-2 focus:ring-accent/40"
              value={filters.hasContact}
              onChange={(event) => updateFilters({ hasContact: event.target.value as FilterState["hasContact"] })}
            >
              <option value="all">Any</option>
              <option value="yes">Has contact</option>
              <option value="no">No contact</option>
            </select>
          </div>

          <div className="space-y-2">
            <label htmlFor="applications-sort" className="block text-sm font-medium text-foreground">
              Sort
            </label>
            <div className="flex gap-2">
              <select
                id="applications-sort"
                className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground focus:border-accent focus:ring-2 focus:ring-accent/40"
                value={filters.sortBy}
                onChange={(event) => updateFilters({ sortBy: event.target.value as FilterState["sortBy"] })}
              >
                <option value="discovered_at">Discovered</option>
                <option value="company">Company</option>
                <option value="status">Status</option>
              </select>
              <select
                aria-label="Sort direction"
                className="block h-11 rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground focus:border-accent focus:ring-2 focus:ring-accent/40"
                value={filters.sortDir}
                onChange={(event) => updateFilters({ sortDir: event.target.value as FilterState["sortDir"] })}
              >
                <option value="desc">Desc</option>
                <option value="asc">Asc</option>
              </select>
            </div>
          </div>

          <FormField
            id="applications-discovered-from"
            label="Discovered from"
            type="datetime-local"
            value={filters.discoveredFrom}
            onChange={(event) => updateFilters({ discoveredFrom: event.target.value })}
          />

          <FormField
            id="applications-discovered-to"
            label="Discovered to"
            type="datetime-local"
            value={filters.discoveredTo}
            onChange={(event) => updateFilters({ discoveredTo: event.target.value })}
          />

          <div className="space-y-2 sm:col-span-2">
            <p className="block text-sm font-medium text-foreground">Status</p>
            <div className="flex flex-wrap gap-2">
              {statusOptions.map((status) => {
                const active = filters.statuses.includes(status);
                return (
                  <Button
                    key={status}
                    size="sm"
                    variant={active ? "secondary" : "ghost"}
                    onClick={() => toggleStatusFilter(status)}
                    className={active ? "ring-1 ring-accent/45" : ""}
                  >
                    {status}
                  </Button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2 sm:col-span-2">
            <p className="block text-sm font-medium text-foreground">Source</p>
            <div className="flex flex-wrap gap-2">
              {sourceOptions.map((source) => {
                const active = filters.sources.includes(source);
                return (
                  <Button
                    key={source}
                    size="sm"
                    variant={active ? "secondary" : "ghost"}
                    onClick={() => toggleSourceFilter(source)}
                    className={active ? "ring-1 ring-accent/45" : ""}
                  >
                    {source}
                  </Button>
                );
              })}
            </div>
          </div>
        </div>
      </Card>

      <Card className="space-y-4" variant="elevated">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-2xl font-semibold text-foreground">Application review</h2>
            <p className="mt-1 text-sm text-muted">
              Review opportunities, click out for manual submission, or queue selected rows for autonomous apply.
            </p>
          </div>
          <Button
            loading={running || isPostRunRefreshing}
            loadingText={isPostRunRefreshing ? "Refreshing results..." : "Running agent..."}
            onClick={async () => {
              try {
                await triggerRunAgent();
              } catch (err: unknown) {
                setError(err instanceof Error ? err.message : "Could not run agent.");
              }
            }}
          >
            Run agent now
          </Button>
        </div>

        <div className="flex flex-col gap-2 rounded-xl2 border border-border/70 bg-surfaceAlt/45 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={selectAllEligibleOnPage}>
              Select eligible on page
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSelectedIds([]);
                setSelectionError("");
              }}
            >
              Clear selection
            </Button>
            <p className="text-xs text-muted">Selected: {selectedIds.length}/{MAX_BULK_SELECTION}</p>
          </div>
          <Button
            loading={bulkApplying}
            loadingText="Queuing apply..."
            onClick={handleBulkApply}
            disabled={!selectedIds.length}
          >
            Auto-apply selected
          </Button>
        </div>

        {selectionError && <InlineAlert variant="error">{selectionError}</InlineAlert>}
        {error && <InlineAlert variant="error">{error}</InlineAlert>}
        {notice && <InlineAlert variant={notice.variant}>{notice.message}</InlineAlert>}

        {loading ? (
          <LoadingState label="Loading applications..." className="min-h-[240px]" />
        ) : (
          <DataTable
            data={apps}
            columns={columns}
            rowKey={(app) => app.id}
            emptyState={
              <EmptyState
                title="No applications found"
                description="Try widening filters or run the agent to discover new opportunities."
                action={
                  <Button
                    loading={running || isPostRunRefreshing}
                    loadingText={isPostRunRefreshing ? "Refreshing results..." : "Running agent..."}
                    onClick={async () => {
                      try {
                        await triggerRunAgent();
                      } catch (err: unknown) {
                        setError(err instanceof Error ? err.message : "Could not run agent.");
                      }
                    }}
                  >
                    Run agent now
                  </Button>
                }
              />
            }
          />
        )}

        <div className="flex items-center justify-between border-t border-border/70 pt-3">
          <p className="text-sm text-muted">
            Page {currentPage} of {totalPages} ({totalCount} total)
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={!canGoPrevious}
              onClick={() => {
                setOffset((current) => Math.max(0, current - PAGE_SIZE));
                setSelectedIds([]);
              }}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!canGoNext}
              onClick={() => {
                setOffset((current) => current + PAGE_SIZE);
                setSelectedIds([]);
              }}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>
    </AppShell>
  );
}

export default function ApplicationsPage() {
  return <ApplicationsInner />;
}
