import { Button } from "@/components/ui/Button";

type ApplicationsBulkActionsProps = {
  selectedCount: number;
  maxSelection: number;
  bulkApplying: boolean;
  onSelectEligibleOnPage: () => void;
  onClearSelection: () => void;
  onBulkApply: () => void;
};

export function ApplicationsBulkActions({
  selectedCount,
  maxSelection,
  bulkApplying,
  onSelectEligibleOnPage,
  onClearSelection,
  onBulkApply,
}: ApplicationsBulkActionsProps) {
  return (
    <div className="flex flex-col gap-2.5 rounded-xl2 border border-border/70 bg-surfaceAlt/45 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <Button variant="secondary" size="sm" onClick={onSelectEligibleOnPage}>
          Select eligible on page
        </Button>
        <Button variant="ghost" size="sm" onClick={onClearSelection}>
          Clear selection
        </Button>
        <p className="text-xs text-muted">Selected: {selectedCount}/{maxSelection}</p>
      </div>
      <Button loading={bulkApplying} loadingText="Queuing apply..." onClick={onBulkApply} disabled={!selectedCount}>
        Auto-apply selected
      </Button>
    </div>
  );
}
