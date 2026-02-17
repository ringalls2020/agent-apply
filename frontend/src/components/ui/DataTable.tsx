import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type DataTableColumn<T> = {
  id: string;
  header: string;
  render: (row: T) => ReactNode;
  mobileLabel?: string;
  className?: string;
};

type DataTableProps<T> = {
  data: T[];
  columns: DataTableColumn<T>[];
  rowKey: (row: T) => string;
  className?: string;
  emptyState?: ReactNode;
  renderMobileRow?: (row: T) => ReactNode;
};

export function DataTable<T>({
  data,
  columns,
  rowKey,
  className,
  emptyState,
  renderMobileRow,
}: DataTableProps<T>) {
  if (!data.length) {
    return <>{emptyState ?? null}</>;
  }

  return (
    <div className={cn("overflow-hidden rounded-2xl border border-border/80 bg-surface/55", className)}>
      <div className="hidden overflow-x-auto md:block">
        <table className="min-w-full divide-y divide-border/75">
          <thead className="bg-surfaceAlt/70">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.id}
                  scope="col"
                  className={cn("px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-muted", column.className)}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/65">
            {data.map((row) => (
              <tr key={rowKey(row)} className="transition-colors duration-250 hover:bg-surfaceAlt/40">
                {columns.map((column) => (
                  <td key={column.id} className={cn("px-4 py-3 text-sm text-foreground", column.className)}>
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="space-y-3 p-3 md:hidden">
        {data.map((row) =>
          renderMobileRow ? (
            <div key={rowKey(row)}>{renderMobileRow(row)}</div>
          ) : (
            <article key={rowKey(row)} className="rounded-xl2 border border-border/80 bg-surfaceAlt/55 p-3">
              {columns.map((column) => (
                <div key={column.id} className="grid grid-cols-[96px_minmax(0,1fr)] gap-2.5 py-1.5">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted">
                    {column.mobileLabel ?? column.header}
                  </p>
                  <div className="text-sm text-foreground text-wrap-anywhere">{column.render(row)}</div>
                </div>
              ))}
            </article>
          ),
        )}
      </div>
    </div>
  );
}
