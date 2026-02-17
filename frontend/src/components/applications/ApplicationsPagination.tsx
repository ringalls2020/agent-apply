import { Button } from "@/components/ui/Button";

type ApplicationsPaginationProps = {
  currentPage: number;
  totalPages: number;
  totalCount: number;
  canGoPrevious: boolean;
  canGoNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
};

export function ApplicationsPagination({
  currentPage,
  totalPages,
  totalCount,
  canGoPrevious,
  canGoNext,
  onPrevious,
  onNext,
}: ApplicationsPaginationProps) {
  return (
    <div className="flex flex-col gap-3 border-t border-border/70 pt-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted text-wrap-anywhere">
        Page {currentPage} of {totalPages} ({totalCount} total)
      </p>
      <div className="grid w-full grid-cols-2 gap-2 sm:w-auto sm:flex sm:items-center">
        <Button variant="secondary" size="sm" disabled={!canGoPrevious} onClick={onPrevious}>
          Previous
        </Button>
        <Button variant="secondary" size="sm" disabled={!canGoNext} onClick={onNext}>
          Next
        </Button>
      </div>
    </div>
  );
}
