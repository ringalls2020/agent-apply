"use client";

import { useMutation, useQuery } from "@apollo/client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { ApplicationMobileCard } from "@/components/applications/ApplicationMobileCard";
import { ApplicationsBulkActions } from "@/components/applications/ApplicationsBulkActions";
import { ApplicationsFilters } from "@/components/applications/ApplicationsFilters";
import { ApplicationsPagination } from "@/components/applications/ApplicationsPagination";
import {
  MAX_BULK_SELECTION,
  PAGE_SIZE,
  type Application,
  type ApplicationFilterInput,
  defaultFilters,
  formatSubmittedAt,
  isSelectableStatus,
  normalizeStatus,
} from "@/components/applications/types";
import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardDescription, CardTitle } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { StatusPill } from "@/components/ui/StatusPill";
import {
  APPLICATIONS_SEARCH,
  APPLY_SELECTED_APPLICATIONS,
  MARK_APPLICATION_APPLIED,
  MARK_APPLICATION_VIEWED,
  ME,
  PROFILE_SETUP_STATUS,
  RUN_AGENT,
} from "@/graphql/operations";
import { useRequireAuth } from "@/lib/useRequireAuth";

type ApplicationsSearchQuery = {
  applicationsSearch: {
    applications: Application[];
    totalCount: number;
    limit: number;
    offset: number;
  };
};

type UserProfile = {
  fullName: string;
  interests: string[];
  applicationsPerDay: number;
  resumeFilename: string | null;
  autosubmitEnabled: boolean;
};

type MeQuery = {
  me: UserProfile | null;
};

type ProfileSetupStatusQuery = {
  profile: {
    userId: string;
  };
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

function ApplicationsInner() {
  const router = useRouter();
  const runAgentEnabled = process.env.NEXT_PUBLIC_ENABLE_RUN_AGENT_DEV !== "false";
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ variant: "success" | "error"; message: string } | null>(null);
  const [isPostRunRefreshing, setIsPostRunRefreshing] = useState(false);
  const [filters, setFilters] = useState(defaultFilters);
  const [offset, setOffset] = useState(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectionError, setSelectionError] = useState("");
  const [optimisticStatuses, setOptimisticStatuses] = useState<Record<string, string>>({});
  const [rowActionLoading, setRowActionLoading] = useState<Record<string, boolean>>({});
  const [defaultFiltersApplied, setDefaultFiltersApplied] = useState(false);
  const [isGateRedirecting, setIsGateRedirecting] = useState(false);

  const { data: meData, loading: meLoading } = useQuery<MeQuery>(ME, {
    skip: !isAuthenticated,
    fetchPolicy: "network-only",
  });
  const {
    data: profileSetupData,
    loading: profileSetupLoading,
    error: profileSetupError,
  } = useQuery<ProfileSetupStatusQuery>(PROFILE_SETUP_STATUS, {
    skip: !isAuthenticated,
    fetchPolicy: "network-only",
  });
  const profile = meData?.me;
  const profileNotFound = profileSetupError?.graphQLErrors.some(
    (graphQLError) => graphQLError.message === "Profile not found",
  );
  const hasUnknownProfileGateError = Boolean(profileSetupError) && !profileNotFound;
  const profileExists = profileSetupData?.profile?.userId ? true : profileNotFound ? false : null;
  const missingSetupSteps = useMemo(() => {
    if (!profile || profileExists === null) return [];

    const steps: string[] = [];
    if (!profileExists) steps.push("profile");
    const hasResume = Boolean(profile.resumeFilename?.trim());
    if (!hasResume) steps.push("resume");
    const hasInterests = profile.interests.some((interest) => interest.trim().length > 0);
    if (!hasInterests) steps.push("interests");
    return steps;
  }, [profile, profileExists]);
  const isSetupComplete = Boolean(profile && missingSetupSteps.length === 0);

  useEffect(() => {
    if (!isAuthenticated || !profile || profileExists === null || hasUnknownProfileGateError) {
      return;
    }
    if (!missingSetupSteps.length) {
      setIsGateRedirecting(false);
      return;
    }

    const params = new URLSearchParams({
      required: "setup",
      next: "/applications",
      missing: missingSetupSteps.join(","),
    });
    setIsGateRedirecting(true);
    router.replace(`/profile?${params.toString()}`);
  }, [
    hasUnknownProfileGateError,
    isAuthenticated,
    missingSetupSteps,
    profile,
    profileExists,
    router,
  ]);

  useEffect(() => {
    if (!meData?.me || defaultFiltersApplied) return;
    if (!meData.me.autosubmitEnabled) {
      setFilters((current) => ({ ...current, statuses: ["review", "viewed", "failed"] }));
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
    if (filters.includeArchived) input.includeArchived = true;
    if (filters.hasContact === "yes") input.hasContact = true;
    if (filters.hasContact === "no") input.hasContact = false;
    if (filters.discoveredFrom) input.discoveredFrom = filters.discoveredFrom;
    if (filters.discoveredTo) input.discoveredTo = filters.discoveredTo;

    return input;
  }, [filters]);

  const { data, loading, refetch } = useQuery<ApplicationsSearchQuery>(APPLICATIONS_SEARCH, {
    skip: !isAuthenticated || !isSetupComplete,
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

  const apps = useMemo(() => data?.applicationsSearch.applications ?? [], [data?.applicationsSearch.applications]);
  const totalCount = data?.applicationsSearch.totalCount ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => apps.some((app) => app.id === id)));
  }, [apps]);

  const resetToFirstPage = () => {
    setOffset(0);
  };

  const updateFilters = (next: Partial<typeof filters>) => {
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
        statuses: exists ? current.statuses.filter((item) => item !== normalized) : [...current.statuses, normalized],
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
        sources: exists ? current.sources.filter((item) => item !== normalized) : [...current.sources, normalized],
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
      .filter((app) => !app.isArchived && isSelectableStatus(getEffectiveStatus(app)))
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
      setSelectionError("Select at least one review/viewed/failed application first.");
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

  const handleRoleClick = (app: Application) => {
    if (app.isArchived) return;
    const currentStatus = normalizeStatus(getEffectiveStatus(app));
    if (currentStatus !== "review") return;

    setOptimisticStatuses((current) => ({ ...current, [app.id]: "viewed" }));
    void markApplicationViewed({ variables: { applicationId: app.id } }).catch(() => undefined);
  };

  const clearFilters = () => {
    const base = defaultFilters();
    if (!profile?.autosubmitEnabled) {
      base.statuses = ["review", "viewed", "failed"];
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
        const selectable = !app.isArchived && isSelectableStatus(status);
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
      render: (app) => <span className="font-medium text-wrap-anywhere">{app.company}</span>,
    },
    {
      id: "role",
      header: "Role",
      render: (app) => (
        <a
          href={app.jobUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 text-accentSoft text-wrap-anywhere hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
          onClick={() => handleRoleClick(app)}
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
      render: (app) => (
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusPill status={getEffectiveStatus(app)} />
          {app.isArchived && <Badge variant="default">Archived</Badge>}
        </div>
      ),
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
            <p className="font-medium text-foreground text-wrap-anywhere">{app.contactName ?? "Unknown contact"}</p>
            <p className="text-xs text-muted text-wrap-anywhere">{app.contactEmail ?? "No email"}</p>
          </div>
        );
      },
    },
    {
      id: "submitted",
      header: "Submitted",
      render: (app) => <span className="text-muted">{formatSubmittedAt(app.submittedAt ??
        "-"
      )}</span>,
    },
    {
      id: "actions",
      header: "Actions",
      mobileLabel: "Actions",
      render: (app) => {
        const status = normalizeStatus(getEffectiveStatus(app));
        const canMarkApplied = !app.isArchived && (status === "review" || status === "viewed");
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

  if (hasUnknownProfileGateError) {
    return (
      <AppShell>
        <Nav />
        <InlineAlert variant="error" className="mx-auto max-w-3xl">
          Could not verify profile setup right now. Please refresh the page and try again.
        </InlineAlert>
      </AppShell>
    );
  }

  if (meLoading || profileSetupLoading || !profile || profileExists === null || isGateRedirecting || missingSetupSteps.length > 0) {
    const label =
      isGateRedirecting || missingSetupSteps.length > 0
        ? "Redirecting to profile setup..."
        : "Checking profile setup...";
    return (
      <AppShell>
        <LoadingState label={label} />
      </AppShell>
    );
  }

  const canGoPrevious = offset > 0;
  const canGoNext = offset + PAGE_SIZE < totalCount;

  return (
    <AppShell className="pb-8">
      <Nav />

      <section className="mb-5 grid gap-3 sm:gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Card variant="metric" className="space-y-1">
          <CardTitle>Applications</CardTitle>
          <p className="text-2xl font-semibold text-foreground sm:text-3xl">{totalCount}</p>
          <CardDescription>Total records matching current filters</CardDescription>
        </Card>

        <Card variant="metric" className="space-y-1">
          <CardTitle>Daily Rate</CardTitle>
          <p className="text-2xl font-semibold text-foreground sm:text-3xl">{profile?.applicationsPerDay ?? "-"}</p>
          <CardDescription>Configured automation velocity</CardDescription>
        </Card>

        <Card variant="metric" className="space-y-1 sm:col-span-2 xl:col-span-1">
          <CardTitle>Interests</CardTitle>
          <p className="text-sm text-foreground text-wrap-anywhere">
            {profile?.interests?.join(", ") || "No interest tags configured"}
          </p>
          <CardDescription>Active target domains for matching</CardDescription>
        </Card>
      </section>

      <ApplicationsFilters
        filters={filters}
        onClearFilters={clearFilters}
        onKeywordChange={(value) => updateFilters({ q: value })}
        onCompaniesChange={(value) => updateFilters({ companiesText: value })}
        onHasContactChange={(value) => updateFilters({ hasContact: value })}
        onSortByChange={(value) => updateFilters({ sortBy: value })}
        onSortDirChange={(value) => updateFilters({ sortDir: value })}
        onIncludeArchivedChange={(value) => updateFilters({ includeArchived: value })}
        onDiscoveredFromChange={(value) => updateFilters({ discoveredFrom: value })}
        onDiscoveredToChange={(value) => updateFilters({ discoveredTo: value })}
        onToggleStatus={toggleStatusFilter}
        onToggleSource={toggleSourceFilter}
      />

      <Card className="space-y-4" variant="elevated">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-2xl font-semibold text-foreground">Application review</h2>
            <p className="mt-1 text-sm text-muted text-wrap-anywhere">
              Review opportunities, click out for manual submission, or queue selected rows for autonomous apply.
            </p>
          </div>
          {runAgentEnabled ? (
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
          ) : null}
        </div>

        <ApplicationsBulkActions
          selectedCount={selectedIds.length}
          maxSelection={MAX_BULK_SELECTION}
          bulkApplying={bulkApplying}
          onSelectEligibleOnPage={selectAllEligibleOnPage}
          onClearSelection={() => {
            setSelectedIds([]);
            setSelectionError("");
          }}
          onBulkApply={handleBulkApply}
        />

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
            renderMobileRow={(app) => {
              const effectiveStatus = getEffectiveStatus(app);
              const normalizedStatus = normalizeStatus(effectiveStatus);
              return (
                <ApplicationMobileCard
                  app={app}
                  effectiveStatus={effectiveStatus}
                  selected={selectedIds.includes(app.id)}
                  selectable={!app.isArchived && isSelectableStatus(effectiveStatus)}
                  canMarkApplied={!app.isArchived && (normalizedStatus === "review" || normalizedStatus === "viewed")}
                  rowActionLoading={Boolean(rowActionLoading[app.id])}
                  onToggleSelection={(checked) => toggleSelection(app.id, checked)}
                  onRoleClick={() => handleRoleClick(app)}
                  onMarkApplied={() => handleMarkApplied(app.id)}
                  formatSubmittedAt={formatSubmittedAt}
                />
              );
            }}
            emptyState={
              <EmptyState
                title="No applications found"
                description="Try widening filters or run the agent to discover new opportunities."
                action={runAgentEnabled ? (
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
                ) : undefined}
              />
            }
          />
        )}

        <ApplicationsPagination
          currentPage={currentPage}
          totalPages={totalPages}
          totalCount={totalCount}
          canGoPrevious={canGoPrevious}
          canGoNext={canGoNext}
          onPrevious={() => {
            setOffset((current) => Math.max(0, current - PAGE_SIZE));
            setSelectedIds([]);
          }}
          onNext={() => {
            setOffset((current) => current + PAGE_SIZE);
            setSelectedIds([]);
          }}
        />
      </Card>
    </AppShell>
  );
}

export default function ApplicationsPage() {
  return <ApplicationsInner />;
}
