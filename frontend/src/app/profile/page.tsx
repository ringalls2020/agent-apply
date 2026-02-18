"use client";

import { useMutation, useQuery } from "@apollo/client";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { FormField } from "@/components/ui/FormField";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { PROFILE, UPDATE_PROFILE } from "@/graphql/operations";
import { cn } from "@/lib/cn";
import { useRequireAuth } from "@/lib/useRequireAuth";

type ProfileQuery = {
  profile: {
    autosubmitEnabled: boolean;
    phone: string | null;
    city: string | null;
    state: string | null;
    country: string | null;
    linkedinUrl: string | null;
    githubUrl: string | null;
    portfolioUrl: string | null;
    workAuthorization: string | null;
    requiresSponsorship: boolean | null;
    willingToRelocate: boolean | null;
    yearsExperience: number | null;
    writingVoice: string | null;
    coverLetterStyle: string | null;
    achievementsSummary: string | null;
    additionalContext: string | null;
    customAnswers: Array<{
      questionKey: string;
      answer: string;
    }>;
    sensitive: {
      gender: string;
      raceEthnicity: string;
      veteranStatus: string;
      disabilityStatus: string;
    };
  };
};

type FormState = {
  autosubmitEnabled: boolean;
  phone: string;
  city: string;
  state: string;
  country: string;
  linkedinUrl: string;
  githubUrl: string;
  portfolioUrl: string;
  workAuthorization: string;
  requiresSponsorship: boolean;
  willingToRelocate: boolean;
  yearsExperience: string;
  writingVoice: string;
  coverLetterStyle: string;
  achievementsSummary: string;
  additionalContext: string;
  customAnswersText: string;
  gender: string;
  raceEthnicity: string;
  veteranStatus: string;
  disabilityStatus: string;
};

const declineOptions = [
  "decline_to_answer",
  "female",
  "male",
  "non_binary",
  "not_listed",
];

const raceOptions = [
  "decline_to_answer",
  "american_indian_or_alaska_native",
  "asian",
  "black_or_african_american",
  "hispanic_or_latino",
  "native_hawaiian_or_pacific_islander",
  "white",
  "two_or_more_races",
  "not_listed",
];

const veteranOptions = [
  "decline_to_answer",
  "not_a_protected_veteran",
  "protected_veteran",
  "not_listed",
];

const disabilityOptions = [
  "decline_to_answer",
  "yes_i_have_a_disability",
  "no_i_do_not_have_a_disability",
  "not_listed",
];

function defaultState(): FormState {
  return {
    autosubmitEnabled: false,
    phone: "",
    city: "",
    state: "",
    country: "",
    linkedinUrl: "",
    githubUrl: "",
    portfolioUrl: "",
    workAuthorization: "",
    requiresSponsorship: false,
    willingToRelocate: false,
    yearsExperience: "",
    writingVoice: "",
    coverLetterStyle: "",
    achievementsSummary: "",
    additionalContext: "",
    customAnswersText: "",
    gender: "decline_to_answer",
    raceEthnicity: "decline_to_answer",
    veteranStatus: "decline_to_answer",
    disabilityStatus: "decline_to_answer",
  };
}

function ProfileInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const { data, loading, refetch } = useQuery<ProfileQuery>(PROFILE, { skip: !isAuthenticated });
  const [updateProfile, { loading: saving }] = useMutation(UPDATE_PROFILE);
  const [form, setForm] = useState<FormState>(defaultState);
  const [notice, setNotice] = useState<{ variant: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    if (!data?.profile) return;
    const profile = data.profile;
    setForm({
      autosubmitEnabled: profile.autosubmitEnabled,
      phone: profile.phone || "",
      city: profile.city || "",
      state: profile.state || "",
      country: profile.country || "",
      linkedinUrl: profile.linkedinUrl || "",
      githubUrl: profile.githubUrl || "",
      portfolioUrl: profile.portfolioUrl || "",
      workAuthorization: profile.workAuthorization || "",
      requiresSponsorship: profile.requiresSponsorship ?? false,
      willingToRelocate: profile.willingToRelocate ?? false,
      yearsExperience: profile.yearsExperience != null ? String(profile.yearsExperience) : "",
      writingVoice: profile.writingVoice || "",
      coverLetterStyle: profile.coverLetterStyle || "",
      achievementsSummary: profile.achievementsSummary || "",
      additionalContext: profile.additionalContext || "",
      customAnswersText: profile.customAnswers.map((item) => `${item.questionKey}=${item.answer}`).join("\n"),
      gender: profile.sensitive.gender,
      raceEthnicity: profile.sensitive.raceEthnicity,
      veteranStatus: profile.sensitive.veteranStatus,
      disabilityStatus: profile.sensitive.disabilityStatus,
    });
  }, [data]);

  const customAnswers = useMemo(
    () =>
      form.customAnswersText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [questionKey, ...answerParts] = line.split("=");
          return {
            questionKey: questionKey?.trim() || "",
            answer: answerParts.join("=").trim(),
          };
        })
        .filter((item) => item.questionKey && item.answer),
    [form.customAnswersText],
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

      <Card variant="elevated" className="mx-auto w-full max-w-4xl space-y-5 sm:space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">Application profile</h2>
          <p className="mt-1 text-sm text-muted text-wrap-anywhere">
            Configure autosubmit behavior and provide answers used by autonomous application workflows.
          </p>
        </div>

        {loading ? (
          <LoadingState label="Loading profile..." className="min-h-[120px]" />
        ) : (
          <form
            className="space-y-5 sm:space-y-6"
            onSubmit={async (event) => {
              event.preventDefault();
              setNotice(null);
              try {
                await updateProfile({
                  variables: {
                    input: {
                      autosubmitEnabled: form.autosubmitEnabled,
                      phone: form.phone || null,
                      city: form.city || null,
                      state: form.state || null,
                      country: form.country || null,
                      linkedinUrl: form.linkedinUrl || null,
                      githubUrl: form.githubUrl || null,
                      portfolioUrl: form.portfolioUrl || null,
                      workAuthorization: form.workAuthorization || null,
                      requiresSponsorship: form.requiresSponsorship,
                      willingToRelocate: form.willingToRelocate,
                      yearsExperience: form.yearsExperience ? Number(form.yearsExperience) : null,
                      writingVoice: form.writingVoice || null,
                      coverLetterStyle: form.coverLetterStyle || null,
                      achievementsSummary: form.achievementsSummary || null,
                      additionalContext: form.additionalContext || null,
                      customAnswers,
                      sensitive: {
                        gender: form.gender,
                        raceEthnicity: form.raceEthnicity,
                        veteranStatus: form.veteranStatus,
                        disabilityStatus: form.disabilityStatus,
                      },
                    },
                  },
                });
                await refetch();
                setNotice({ variant: "success", message: "Profile saved." });
              } catch (error: unknown) {
                setNotice({
                  variant: "error",
                  message: error instanceof Error ? error.message : "Could not save profile.",
                });
              }
            }}
          >
            <section className="rounded-xl2 border border-border/80 bg-surfaceAlt/55 p-3.5 sm:p-4">
              <label className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={form.autosubmitEnabled}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, autosubmitEnabled: event.target.checked }))
                  }
                  className={cn(
                    "mt-1 h-4 w-4 rounded border-border bg-surfaceAlt",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
                  )}
                />
                <div>
                  <p className="text-sm font-semibold text-foreground">Enable autosubmit</p>
                  <p className="text-xs text-muted text-wrap-anywhere">
                    When enabled, running the GraphQL `runAgent` mutation triggers autonomous apply attempts immediately after matching.
                  </p>
                </div>
              </label>
            </section>

            <section className="grid gap-3 sm:gap-4 sm:grid-cols-2">
              <FormField
                id="profile-phone"
                label="Phone"
                value={form.phone}
                onChange={(e) => setForm((current) => ({ ...current, phone: e.target.value }))}
              />
              <FormField
                id="profile-work-auth"
                label="Work Authorization"
                value={form.workAuthorization}
                onChange={(e) => setForm((current) => ({ ...current, workAuthorization: e.target.value }))}
                hint="Example: US Citizen, Green Card, EAD"
              />
              <FormField
                id="profile-city"
                label="City"
                value={form.city}
                onChange={(e) => setForm((current) => ({ ...current, city: e.target.value }))}
              />
              <FormField
                id="profile-state"
                label="State"
                value={form.state}
                onChange={(e) => setForm((current) => ({ ...current, state: e.target.value }))}
              />
              <FormField
                id="profile-country"
                label="Country"
                value={form.country}
                onChange={(e) => setForm((current) => ({ ...current, country: e.target.value }))}
              />
              <FormField
                id="profile-years"
                type="number"
                min={0}
                max={80}
                label="Years of Experience"
                value={form.yearsExperience}
                onChange={(e) => setForm((current) => ({ ...current, yearsExperience: e.target.value }))}
              />
            </section>

            <section className="grid gap-3 sm:gap-4 sm:grid-cols-3">
              <FormField
                id="profile-linkedin"
                label="LinkedIn URL"
                value={form.linkedinUrl}
                onChange={(e) => setForm((current) => ({ ...current, linkedinUrl: e.target.value }))}
              />
              <FormField
                id="profile-github"
                label="GitHub URL"
                value={form.githubUrl}
                onChange={(e) => setForm((current) => ({ ...current, githubUrl: e.target.value }))}
              />
              <FormField
                id="profile-portfolio"
                label="Portfolio URL"
                value={form.portfolioUrl}
                onChange={(e) => setForm((current) => ({ ...current, portfolioUrl: e.target.value }))}
              />
            </section>

            <section className="grid gap-3 sm:gap-4 sm:grid-cols-2">
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Requires sponsorship</span>
                <select
                  value={form.requiresSponsorship ? "yes" : "no"}
                  onChange={(e) =>
                    setForm((current) => ({ ...current, requiresSponsorship: e.target.value === "yes" }))
                  }
                  className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground"
                >
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Willing to relocate</span>
                <select
                  value={form.willingToRelocate ? "yes" : "no"}
                  onChange={(e) =>
                    setForm((current) => ({ ...current, willingToRelocate: e.target.value === "yes" }))
                  }
                  className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground"
                >
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
              </label>
            </section>

            <section className="grid gap-3 sm:gap-4 sm:grid-cols-2">
              <FormField
                id="profile-writing-voice"
                label="Writing Voice"
                value={form.writingVoice}
                onChange={(e) => setForm((current) => ({ ...current, writingVoice: e.target.value }))}
                hint="Example: concise, analytical, personable"
              />
              <FormField
                id="profile-cover-style"
                label="Cover Letter Style"
                value={form.coverLetterStyle}
                onChange={(e) => setForm((current) => ({ ...current, coverLetterStyle: e.target.value }))}
                hint="Example: formal, story-driven, impact-focused"
              />
            </section>

            <section className="grid gap-3 sm:gap-4">
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Achievements Summary</span>
                <textarea
                  value={form.achievementsSummary}
                  onChange={(e) => setForm((current) => ({ ...current, achievementsSummary: e.target.value }))}
                  rows={4}
                  className="block w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 py-2.5 text-sm text-foreground"
                />
              </label>
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Additional Context</span>
                <textarea
                  value={form.additionalContext}
                  onChange={(e) => setForm((current) => ({ ...current, additionalContext: e.target.value }))}
                  rows={4}
                  className="block w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 py-2.5 text-sm text-foreground"
                />
              </label>
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Custom Answers</span>
                <textarea
                  value={form.customAnswersText}
                  onChange={(e) => setForm((current) => ({ ...current, customAnswersText: e.target.value }))}
                  rows={5}
                  placeholder="question_key=answer"
                  className="block w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 py-2.5 text-sm text-foreground"
                />
                <p className="text-xs text-muted text-wrap-anywhere">
                  One `question_key=answer` per line. Used as overrides before LLM generation.
                </p>
              </label>
            </section>

            <section className="grid gap-3 sm:gap-4 sm:grid-cols-2">
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Gender</span>
                <select
                  value={form.gender}
                  onChange={(e) => setForm((current) => ({ ...current, gender: e.target.value }))}
                  className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground"
                >
                  {declineOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Race / Ethnicity</span>
                <select
                  value={form.raceEthnicity}
                  onChange={(e) => setForm((current) => ({ ...current, raceEthnicity: e.target.value }))}
                  className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground"
                >
                  {raceOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Veteran Status</span>
                <select
                  value={form.veteranStatus}
                  onChange={(e) => setForm((current) => ({ ...current, veteranStatus: e.target.value }))}
                  className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground"
                >
                  {veteranOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span className="block font-medium text-foreground text-wrap-anywhere">Disability Status</span>
                <select
                  value={form.disabilityStatus}
                  onChange={(e) => setForm((current) => ({ ...current, disabilityStatus: e.target.value }))}
                  className="block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground"
                >
                  {disabilityOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            </section>

            {notice && <InlineAlert variant={notice.variant}>{notice.message}</InlineAlert>}

            <Button type="submit" loading={saving} loadingText="Saving profile..." className="w-full sm:w-auto">
              Save profile
            </Button>
          </form>
        )}
      </Card>
    </AppShell>
  );
}

export default function ProfilePage() {
  return <ProfileInner />;
}
