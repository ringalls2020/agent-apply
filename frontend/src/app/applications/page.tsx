"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Button } from "@/components/ui/Button";
import { Card, CardDescription, CardTitle } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { StatusPill } from "@/components/ui/StatusPill";
import { APPLICATIONS, ME, RUN_AGENT } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";
import { useRequireAuth } from "@/lib/useRequireAuth";

type Application = {
  id: string;
  title: string;
  company: string;
  status: string;
  contactName: string | null;
  contactEmail: string | null;
  submittedAt: string;
  jobUrl: string;
};

type ApplicationsQuery = {
  applications: Application[];
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

function formatSubmittedAt(dateString: string) {
  const parsed = new Date(dateString);
  if (Number.isNaN(parsed.getTime())) return "-";
  return parsed.toLocaleString();
}

function ApplicationsInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const [error, setError] = useState("");
  const [isPostRunRefreshing, setIsPostRunRefreshing] = useState(false);
  const { data: meData } = useQuery<MeQuery>(ME, { skip: !isAuthenticated });
  const { data, loading, refetch } = useQuery<ApplicationsQuery>(APPLICATIONS, { skip: !isAuthenticated });
  const [runAgent, { loading: running }] = useMutation(RUN_AGENT);

  const triggerRunAgent = async () => {
    setError("");
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

  const columns = useMemo<DataTableColumn<Application>[]>(
    () => [
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
          >
            {app.title}
            <span aria-hidden="true" className="text-xs text-muted">
              {"->"}
            </span>
          </a>
        ),
      },
      {
        id: "status",
        header: "Status",
        render: (app) => <StatusPill status={app.status} />,
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
    ],
    [],
  );

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

  const apps = data?.applications ?? [];
  const profile = meData?.me;

  return (
    <AppShell className="pb-8">
      <Nav />

      <section className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Card variant="metric" className="space-y-1">
          <CardTitle>Applications</CardTitle>
          <p className="text-3xl font-semibold text-foreground">{apps.length}</p>
          <CardDescription>Total runs captured in your pipeline</CardDescription>
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

      <Card className="space-y-4" variant="elevated">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-2xl font-semibold text-foreground">Application review</h2>
            <p className="mt-1 text-sm text-muted">
              Review all submissions generated by the agent and inspect matched contacts.
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

        {error && <InlineAlert variant="error">{error}</InlineAlert>}

        {loading ? (
          <LoadingState label="Loading applications..." className="min-h-[240px]" />
        ) : (
          <DataTable
            data={apps}
            columns={columns}
            rowKey={(app) => app.id}
            emptyState={
              <EmptyState
                title="No applications yet"
                description="Trigger the automation to generate the first application attempts for your configured interests."
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
      </Card>
    </AppShell>
  );
}

export default function ApplicationsPage() {
  return (
    <ApolloProvider client={getClient()}>
      <ApplicationsInner />
    </ApolloProvider>
  );
}
