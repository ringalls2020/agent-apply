import { type ReactNode, useState } from "react";

import { type Application } from "@/components/applications/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { StatusPill } from "@/components/ui/StatusPill";

type ApplicationMobileCardProps = {
  app: Application;
  effectiveStatus: string;
  selected: boolean;
  selectable: boolean;
  canMarkApplied: boolean;
  rowActionLoading: boolean;
  onToggleSelection: (checked: boolean) => void;
  onRoleClick: () => void;
  onMarkApplied: () => Promise<void> | void;
  formatSubmittedAt: (dateString: string) => string;
};

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2.5">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</p>
      <div className="text-sm text-foreground text-wrap-anywhere">{value}</div>
    </div>
  );
}

export function ApplicationMobileCard({
  app,
  effectiveStatus,
  selected,
  selectable,
  canMarkApplied,
  rowActionLoading,
  onToggleSelection,
  onRoleClick,
  onMarkApplied,
  formatSubmittedAt,
}: ApplicationMobileCardProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <article className="rounded-xl2 border border-border/80 bg-surfaceAlt/55 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">{app.company}</p>
            {app.isArchived && <Badge variant="default">Archived</Badge>}
          </div>
          <a
            href={app.jobUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex text-sm font-medium text-accentSoft text-wrap-anywhere hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
            onClick={onRoleClick}
          >
            {app.title}
          </a>
        </div>
        <input
          aria-label={`Select ${app.title}`}
          type="checkbox"
          checked={selected}
          disabled={app.isArchived || (!selectable && !selected)}
          onChange={(event) => onToggleSelection(event.target.checked)}
          className="mt-0.5 size-4 rounded border-border bg-surfaceAlt/70 text-accent focus-visible:ring-2 focus-visible:ring-accent/45"
        />
      </div>

      <div className="mt-3 flex items-center justify-between gap-3">
        <StatusPill status={effectiveStatus} />
        <Button variant="ghost" size="sm" onClick={() => setExpanded((current) => !current)}>
          {expanded ? "Hide details" : "Show details"}
        </Button>
      </div>
      {app.isArchived && (
        <p className="mt-2 text-xs text-muted">
          This listing is archived and cannot be applied to.
        </p>
      )}

      {expanded && (
        <div className="mt-3 space-y-2.5 border-t border-border/70 pt-3">
          <DetailRow label="Source" value={<span className="capitalize">{app.source}</span>} />
          <DetailRow
            label="Contact"
            value={
              app.contactName || app.contactEmail ? (
                <div className="space-y-0.5">
                  <p className="font-medium text-foreground text-wrap-anywhere">{app.contactName ?? "Unknown contact"}</p>
                  <p className="text-xs text-muted text-wrap-anywhere">{app.contactEmail ?? "No email"}</p>
                </div>
              ) : (
                <span className="text-muted">No contact found</span>
              )
            }
          />
          <DetailRow label="Submitted" value={<span className="text-muted">{formatSubmittedAt(app.submittedAt)}</span>} />
          <DetailRow
            label="Actions"
            value={
              canMarkApplied ? (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={rowActionLoading}
                  loadingText="Saving..."
                  onClick={() => {
                    void onMarkApplied();
                  }}
                >
                  Mark applied
                </Button>
              ) : (
                <span className="text-xs text-muted">-</span>
              )
            }
          />
        </div>
      )}
    </article>
  );
}
