"use client";

import { ApolloProvider, useMutation, useQuery } from "@apollo/client";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { FormField } from "@/components/ui/FormField";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { ME, UPDATE_PREFERENCES } from "@/graphql/operations";
import { getClient } from "@/lib/apollo";
import { useRequireAuth } from "@/lib/useRequireAuth";

type UserProfile = {
  interests: string[];
  applicationsPerDay: number;
};

type MeQuery = {
  me: UserProfile | null;
};

function PreferencesInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const { data, refetch } = useQuery<MeQuery>(ME, { skip: !isAuthenticated });
  const [savePreferences, { loading }] = useMutation(UPDATE_PREFERENCES);
  const [interests, setInterests] = useState("ai,automation");
  const [applicationsPerDay, setApplicationsPerDay] = useState(3);
  const [notice, setNotice] = useState<{ variant: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    if (data?.me) {
      setInterests(data.me.interests.join(", "));
      setApplicationsPerDay(data.me.applicationsPerDay);
    }
  }, [data]);

  const parsedInterests = useMemo(
    () =>
      interests
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    [interests],
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

  return (
    <AppShell className="pb-8">
      <Nav />

      <Card variant="elevated" className="mx-auto w-full max-w-3xl space-y-5">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">User preferences</h2>
          <p className="mt-1 text-sm text-muted">
            Tune which opportunities are targeted and how aggressively the automation applies each day.
          </p>
        </div>

        <form
          className="space-y-4"
          onSubmit={async (event) => {
            event.preventDefault();
            setNotice(null);
            try {
              await savePreferences({
                variables: {
                  interests: parsedInterests,
                  applicationsPerDay,
                },
              });
              await refetch();
              setNotice({ variant: "success", message: "Preferences saved." });
            } catch (err: unknown) {
              setNotice({
                variant: "error",
                message: err instanceof Error ? err.message : "Could not save preferences.",
              });
            }
          }}
        >
          <FormField
            id="preferences-interests"
            label="Interests (comma separated)"
            hint="Examples: ai, data platforms, security"
            value={interests}
            onChange={(e) => setInterests(e.target.value)}
            required
          />

          <FormField
            id="preferences-rate"
            label="Applications per day"
            type="number"
            min={1}
            max={30}
            value={applicationsPerDay}
            onChange={(e) => {
              const nextValue = Number(e.target.value);
              setApplicationsPerDay(Number.isNaN(nextValue) ? 1 : nextValue);
            }}
            hint="Choose a value between 1 and 30."
            required
          />

          {!!parsedInterests.length && (
            <div className="rounded-xl2 border border-border/80 bg-surfaceAlt/55 p-3">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Parsed interest tags</p>
              <div className="flex flex-wrap gap-2">
                {parsedInterests.map((interest) => (
                  <span
                    key={interest}
                    className="inline-flex rounded-full border border-accent/35 bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accentSoft"
                  >
                    {interest}
                  </span>
                ))}
              </div>
            </div>
          )}

          {notice && <InlineAlert variant={notice.variant}>{notice.message}</InlineAlert>}

          <Button type="submit" loading={loading} loadingText="Saving...">
            Save preferences
          </Button>
        </form>
      </Card>
    </AppShell>
  );
}

export default function PreferencesPage() {
  return (
    <ApolloProvider client={getClient()}>
      <PreferencesInner />
    </ApolloProvider>
  );
}
