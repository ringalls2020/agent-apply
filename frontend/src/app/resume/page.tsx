"use client";

import { useMutation, useQuery } from "@apollo/client";
import { useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Nav } from "@/components/Nav";
import { Card } from "@/components/ui/Card";
import { InlineAlert } from "@/components/ui/InlineAlert";
import { LoadingState } from "@/components/ui/LoadingState";
import { ME, UPLOAD_RESUME } from "@/graphql/operations";
import { useRequireAuth } from "@/lib/useRequireAuth";

type MeQuery = {
  me: {
    resumeFilename: string | null;
  } | null;
};

function ResumeInner() {
  const { isCheckingAuth, isAuthenticated } = useRequireAuth();
  const [notice, setNotice] = useState<{ variant: "success" | "error"; message: string } | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const { data, refetch } = useQuery<MeQuery>(ME, { skip: !isAuthenticated });
  const [uploadResume, { loading }] = useMutation(UPLOAD_RESUME);

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
          <h2 className="text-2xl font-semibold text-foreground">Resume upload</h2>
          <p className="mt-1 text-sm text-muted text-wrap-anywhere">
            Upload your latest resume so matching and application quality stay aligned with your profile.
          </p>
        </div>

        <label className="relative flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-accent/45 bg-accent/5 p-5 text-center transition duration-250 hover:bg-accent/10 focus-within:ring-2 focus-within:ring-accent/40 sm:p-7">
          <input
            type="file"
            accept=".txt,.md"
            className="absolute inset-0 cursor-pointer opacity-0"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              setSelectedFileName(file.name);
              setNotice(null);

              try {
                const text = await file.text();
                await uploadResume({ variables: { filename: file.name, text } });
                await refetch();
                setNotice({ variant: "success", message: "Resume uploaded successfully." });
              } catch (err: unknown) {
                setNotice({
                  variant: "error",
                  message: err instanceof Error ? err.message : "Could not upload resume.",
                });
              }
            }}
          />

          <p className="text-sm font-semibold text-accentSoft text-wrap-anywhere">Drop a resume file or click to browse</p>
          <p className="mt-1 text-xs text-muted text-wrap-anywhere">Accepted formats: .txt, .md</p>
        </label>

        <div className="rounded-xl2 border border-border/80 bg-surfaceAlt/55 p-3.5 sm:p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">Current resume</p>
          <p className="mt-1 text-sm text-foreground text-wrap-anywhere">{data?.me?.resumeFilename || "No resume uploaded"}</p>
          {selectedFileName && <p className="mt-2 text-xs text-muted text-wrap-anywhere">Last selected file: {selectedFileName}</p>}
        </div>

        {notice && <InlineAlert variant={notice.variant}>{notice.message}</InlineAlert>}
        {loading && <LoadingState label="Uploading resume..." className="min-h-[92px]" />}
      </Card>
    </AppShell>
  );
}

export default function ResumePage() {
  return <ResumeInner />;
}
