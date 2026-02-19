"use client";

import { useMutation, useQuery } from "@apollo/client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { FormField } from "@/components/ui/FormField";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { Input } from "@/components/ui/Input";
import { LoadingState } from "@/components/ui/LoadingState";
import {
  CONFIRM_INFERRED_PREFERENCES,
  INFERRED_PREFERENCES,
  ME,
  UPDATE_PREFERENCES,
} from "@/graphql/operations";
import { useRequireAuth } from "@/lib/useRequireAuth";

type UserProfile = {
  interests: string[];
  locations: string[];
  applicationsPerDay: number;
};

type MeQuery = {
  me: UserProfile | null;
};

type InferredPreference = {
  edgeId: string;
  nodeId: string;
  nodeType: string;
  canonicalKey: string;
  label: string;
  confidence: number;
  weight: number;
  hardConstraint: boolean;
  rationale?: string | null;
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "EDITED";
  lastDecisionAt?: string | null;
};

type InferredPreferencesQuery = {
  inferredPreferences: InferredPreference[];
};

type ConfirmInferredPreferencesMutation = {
  confirmInferredPreferences: {
    acceptedCount: number;
    rejectedCount: number;
    editedCount: number;
    remainingPendingCount: number;
    inferredPreferences: InferredPreference[];
  };
};

type DecisionDraft = {
  decision: "ACCEPT" | "REJECT" | "EDIT";
  editedLabel?: string;
};

function PreferencesInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const { data, refetch } = useQuery<MeQuery>(ME, { skip: !isAuthenticated });
  const {
    data: inferredData,
    loading: inferredLoading,
    refetch: refetchInferred,
  } = useQuery<InferredPreferencesQuery>(INFERRED_PREFERENCES, {
    skip: !isAuthenticated,
    variables: { status: "PENDING" },
  });
  const [savePreferences, { loading }] = useMutation(UPDATE_PREFERENCES);
  const [confirmInferred, { loading: confirming }] =
    useMutation<ConfirmInferredPreferencesMutation>(CONFIRM_INFERRED_PREFERENCES);
  const [interests, setInterests] = useState("ai,automation");
  const [applicationsPerDay, setApplicationsPerDay] = useState(3);
  const [notice, setNotice] = useState<{ variant: "success" | "error"; message: string } | null>(null);
  const [inferredNotice, setInferredNotice] = useState<{
    variant: "success" | "error";
    message: string;
  } | null>(null);
  const [stagedDecisions, setStagedDecisions] = useState<Record<string, DecisionDraft>>({});
  const [editDrafts, setEditDrafts] = useState<Record<string, string>>({});

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
  const savedLocations = data?.me?.locations ?? [];
  const pendingInferred = useMemo(
    () =>
      (inferredData?.inferredPreferences ?? []).filter(
        (item) => item.nodeType === "skill" || item.nodeType === "location",
      ),
    [inferredData],
  );
  const stagedActionCount = Object.keys(stagedDecisions).length;

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

      <Card variant="elevated" className="mx-auto w-full max-w-3xl space-y-4 sm:space-y-5">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">User preferences</h2>
          <p className="mt-1 text-sm text-muted text-wrap-anywhere">
            Tune which opportunities are targeted and how aggressively the automation applies each day.
          </p>
        </div>

        <form
          className="space-y-3.5 sm:space-y-4"
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
                    className="inline-flex rounded-full border border-accent/35 bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accentSoft text-wrap-anywhere"
                  >
                    {interest}
                  </span>
                ))}
              </div>
            </div>
          )}

          {notice && <InlineAlert variant={notice.variant}>{notice.message}</InlineAlert>}

          <Button type="submit" loading={loading} loadingText="Saving..." className="w-full sm:w-auto">
            Save preferences
          </Button>
        </form>
      </Card>

      <Card variant="elevated" className="mx-auto mt-5 w-full max-w-3xl space-y-4 sm:space-y-5">
        <div>
          <h3 className="text-xl font-semibold text-foreground">Inferred from resume</h3>
          <p className="mt-1 text-sm text-muted text-wrap-anywhere">
            Review inferred skills and locations before they influence matching.
          </p>
        </div>

        {inferredLoading ? (
          <LoadingState label="Loading inferred preferences..." />
        ) : pendingInferred.length === 0 ? (
          <InlineAlert variant="info">No pending inferred preferences.</InlineAlert>
        ) : (
          <div className="space-y-3">
            {pendingInferred.map((item) => {
              const staged = stagedDecisions[item.edgeId];
              const editValue = editDrafts[item.edgeId] ?? item.label;
              const isEdit = staged?.decision === "EDIT";
              return (
                <div
                  key={item.edgeId}
                  className="rounded-xl2 border border-border/75 bg-surfaceAlt/55 p-3.5"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1">
                      <p className="text-sm font-semibold text-foreground">{item.label}</p>
                      <p className="text-xs text-muted">
                        {item.nodeType} · confidence {(item.confidence * 100).toFixed(0)}%
                      </p>
                      {item.rationale && (
                        <p className="text-xs text-muted text-wrap-anywhere">{item.rationale}</p>
                      )}
                      {staged && (
                        <p className="text-xs font-semibold text-accentSoft">
                          Staged: {staged.decision.toLowerCase()}
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant={staged?.decision === "ACCEPT" ? "primary" : "secondary"}
                        size="sm"
                        onClick={() => {
                          setStagedDecisions((prev) => ({
                            ...prev,
                            [item.edgeId]: { decision: "ACCEPT" },
                          }));
                        }}
                      >
                        Accept
                      </Button>
                      <Button
                        type="button"
                        variant={staged?.decision === "REJECT" ? "danger" : "ghost"}
                        size="sm"
                        onClick={() => {
                          setStagedDecisions((prev) => ({
                            ...prev,
                            [item.edgeId]: { decision: "REJECT" },
                          }));
                        }}
                      >
                        Reject
                      </Button>
                    </div>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Input
                      value={editValue}
                      onChange={(event) =>
                        setEditDrafts((prev) => ({ ...prev, [item.edgeId]: event.target.value }))
                      }
                      placeholder="Edit inferred value"
                      className="max-w-sm"
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant={isEdit ? "primary" : "secondary"}
                      onClick={() => {
                        const editedLabel = editValue.trim();
                        if (!editedLabel) {
                          return;
                        }
                        setStagedDecisions((prev) => ({
                          ...prev,
                          [item.edgeId]: { decision: "EDIT", editedLabel },
                        }));
                      }}
                    >
                      Edit
                    </Button>
                    {staged && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setStagedDecisions((prev) => {
                            const next = { ...prev };
                            delete next[item.edgeId];
                            return next;
                          });
                        }}
                      >
                        Clear
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {inferredNotice && <InlineAlert variant={inferredNotice.variant}>{inferredNotice.message}</InlineAlert>}

        <Button
          type="button"
          loading={confirming}
          loadingText="Submitting..."
          disabled={stagedActionCount === 0}
          onClick={async () => {
            setInferredNotice(null);
            try {
              const actions = Object.entries(stagedDecisions).map(([edgeId, action]) => ({
                edgeId,
                decision: action.decision,
                editedLabel: action.decision === "EDIT" ? action.editedLabel : null,
              }));
              const result = await confirmInferred({
                variables: { actions },
              });
              const payload = result.data?.confirmInferredPreferences;
              await Promise.all([refetch(), refetchInferred()]);
              setStagedDecisions({});
              setEditDrafts({});
              setInferredNotice({
                variant: "success",
                message: payload
                  ? `Saved decisions: accepted ${payload.acceptedCount}, edited ${payload.editedCount}, rejected ${payload.rejectedCount}.`
                  : "Saved inferred preference decisions.",
              });
            } catch (err: unknown) {
              setInferredNotice({
                variant: "error",
                message: err instanceof Error ? err.message : "Could not save inferred preference decisions.",
              });
            }
          }}
        >
          Confirm staged decisions ({stagedActionCount})
        </Button>
      </Card>

      <Card variant="base" className="mx-auto mt-5 w-full max-w-3xl space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-foreground">Saved locations</h3>
          <p className="mt-1 text-sm text-muted text-wrap-anywhere">
            Location preferences shown here are read-only. Manage location details in{" "}
            <Link href="/profile" className="text-accentSoft underline-offset-2 hover:underline">
              Profile
            </Link>
            .
          </p>
        </div>
        {savedLocations.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {savedLocations.map((location) => (
              <span
                key={location}
                className="inline-flex rounded-full border border-accent/35 bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accentSoft text-wrap-anywhere"
              >
                {location}
              </span>
            ))}
          </div>
        ) : (
          <InlineAlert variant="info">No saved locations.</InlineAlert>
        )}
      </Card>
    </AppShell>
  );
}

export default function PreferencesPage() {
  return <PreferencesInner />;
}
