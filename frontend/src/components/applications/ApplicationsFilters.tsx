import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { FormField } from "@/components/ui/FormField";
import { cn } from "@/lib/cn";

import { type FilterState, sourceOptions, statusOptions } from "@/components/applications/types";

type ApplicationsFiltersProps = {
  filters: FilterState;
  onClearFilters: () => void;
  onKeywordChange: (value: string) => void;
  onCompaniesChange: (value: string) => void;
  onHasContactChange: (value: FilterState["hasContact"]) => void;
  onSortByChange: (value: FilterState["sortBy"]) => void;
  onSortDirChange: (value: FilterState["sortDir"]) => void;
  onDiscoveredFromChange: (value: string) => void;
  onDiscoveredToChange: (value: string) => void;
  onToggleStatus: (status: string) => void;
  onToggleSource: (source: string) => void;
};

const selectClasses =
  "block h-11 w-full rounded-xl2 border border-border bg-surfaceAlt/70 px-3.5 text-sm text-foreground focus:border-accent focus:ring-2 focus:ring-accent/40";

export function ApplicationsFilters({
  filters,
  onClearFilters,
  onKeywordChange,
  onCompaniesChange,
  onHasContactChange,
  onSortByChange,
  onSortDirChange,
  onDiscoveredFromChange,
  onDiscoveredToChange,
  onToggleStatus,
  onToggleSource,
}: ApplicationsFiltersProps) {
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

  return (
    <Card className="mb-4 space-y-4" variant="elevated">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-foreground">Filters</h2>
          <p className="mt-1 text-sm text-muted text-wrap-anywhere">
            Narrow the review queue, then bulk auto-apply selected rows.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="sm:hidden"
            aria-expanded={mobileFiltersOpen}
            aria-controls="applications-filter-fields"
            onClick={() => setMobileFiltersOpen((current) => !current)}
          >
            {mobileFiltersOpen ? "Hide filters" : "Show filters"}
          </Button>
          <Button variant="secondary" size="sm" onClick={onClearFilters} className={cn(!mobileFiltersOpen && "hidden", "sm:inline-flex")}>
            Clear filters
          </Button>
        </div>
      </div>

      <div
        id="applications-filter-fields"
        className={cn("gap-3 sm:gap-4 sm:grid-cols-2 lg:grid-cols-4 sm:grid", mobileFiltersOpen ? "grid" : "hidden")}
      >
        <FormField
          id="applications-search"
          label="Keyword"
          placeholder="Role, company, contact..."
          value={filters.q}
          onChange={(event) => onKeywordChange(event.target.value)}
        />

        <FormField
          id="applications-companies"
          label="Companies"
          placeholder="Comma-separated"
          value={filters.companiesText}
          onChange={(event) => onCompaniesChange(event.target.value)}
        />

        <div className="space-y-2">
          <label htmlFor="applications-contact" className="block text-sm font-medium text-foreground text-wrap-anywhere">
            Contact
          </label>
          <select
            id="applications-contact"
            className={selectClasses}
            value={filters.hasContact}
            onChange={(event) => onHasContactChange(event.target.value as FilterState["hasContact"])}
          >
            <option value="all">Any</option>
            <option value="yes">Has contact</option>
            <option value="no">No contact</option>
          </select>
        </div>

        <div className="space-y-2">
          <label htmlFor="applications-sort" className="block text-sm font-medium text-foreground text-wrap-anywhere">
            Sort
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              id="applications-sort"
              className={selectClasses}
              value={filters.sortBy}
              onChange={(event) => onSortByChange(event.target.value as FilterState["sortBy"])}
            >
              <option value="discovered_at">Discovered</option>
              <option value="company">Company</option>
              <option value="status">Status</option>
            </select>
            <select
              aria-label="Sort direction"
              className={`${selectClasses} sm:max-w-[132px]`}
              value={filters.sortDir}
              onChange={(event) => onSortDirChange(event.target.value as FilterState["sortDir"])}
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
          onChange={(event) => onDiscoveredFromChange(event.target.value)}
        />

        <FormField
          id="applications-discovered-to"
          label="Discovered to"
          type="datetime-local"
          value={filters.discoveredTo}
          onChange={(event) => onDiscoveredToChange(event.target.value)}
        />

        <div className="space-y-2 sm:col-span-2">
          <p className="block text-sm font-medium text-foreground text-wrap-anywhere">Status</p>
          <div className="flex flex-wrap gap-2">
            {statusOptions.map((status) => {
              const active = filters.statuses.includes(status);
              return (
                <Button
                  key={status}
                  size="sm"
                  variant={active ? "secondary" : "ghost"}
                  onClick={() => onToggleStatus(status)}
                  className={active ? "ring-1 ring-accent/45" : ""}
                >
                  {status}
                </Button>
              );
            })}
          </div>
        </div>

        <div className="space-y-2 sm:col-span-2">
          <p className="block text-sm font-medium text-foreground text-wrap-anywhere">Source</p>
          <div className="flex flex-wrap gap-2">
            {sourceOptions.map((source) => {
              const active = filters.sources.includes(source);
              return (
                <Button
                  key={source}
                  size="sm"
                  variant={active ? "secondary" : "ghost"}
                  onClick={() => onToggleSource(source)}
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
  );
}
