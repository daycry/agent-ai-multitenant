import * as React from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/**
 * DataTable — a thin, declarative wrapper over the Table primitive for
 * the common case: a fixed set of columns + an array of rows, with a
 * built-in empty row when there's nothing to show.
 *
 * It is sugar, not a contract: it composes the same Table/TableRow/etc.
 * primitives a page would write by hand, so every header `data-testid`,
 * row `onClick`, etc. still works. Reach for the raw primitives when a
 * table needs per-cell control the column API can't express.
 *
 *   <DataTable
 *     data={runtimes}
 *     getRowKey={(r) => r.id}
 *     rowProps={(r) => ({ "data-runtime": r.id })}
 *     columns={[
 *       { key: "name", header: "Runtime", cell: (r) => r.label },
 *       { key: "actions", header: "Acciones", cell: (r) => <Button…/> },
 *     ]}
 *     emptyMessage="No hay runtimes."
 *   />
 */
export interface DataTableColumn<T> {
  /** Stable key for React + maps a cell to its header. */
  key: string;
  /** Header content. */
  header: React.ReactNode;
  /** Cell renderer for a given row. */
  cell: (row: T, index: number) => React.ReactNode;
  /** Optional className applied to both the header and body cells. */
  className?: string;
  /** Optional className applied only to the header cell. */
  headClassName?: string;
}

interface DataTableProps<T> extends React.TableHTMLAttributes<HTMLTableElement> {
  data: readonly T[];
  columns: ReadonlyArray<DataTableColumn<T>>;
  /** Stable React key per row. Defaults to the array index. */
  getRowKey?: (row: T, index: number) => React.Key;
  /** Extra attributes (e.g. `data-*`, `onClick`) spread onto each `<tr>`. */
  rowProps?: (
    row: T,
    index: number,
  ) => React.HTMLAttributes<HTMLTableRowElement> & Record<`data-${string}`, string | undefined>;
  /** Shown as a single full-width row when `data` is empty. */
  emptyMessage?: React.ReactNode;
  /** Class for the wrapper scroll container (forwarded to Table). */
  wrapperClassName?: string;
}

export function DataTable<T>({
  data,
  columns,
  getRowKey,
  rowProps,
  emptyMessage = "Sin resultados.",
  className,
  wrapperClassName,
  ...tableProps
}: DataTableProps<T>) {
  return (
    <Table className={className} wrapperClassName={wrapperClassName} {...tableProps}>
      <TableHeader>
        <TableRow>
          {columns.map((col) => (
            <TableHead key={col.key} className={cn(col.className, col.headClassName)}>
              {col.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.length === 0 ? (
          <TableRow>
            <TableCell
              colSpan={columns.length}
              className="text-muted-foreground py-6 text-center text-sm"
            >
              {emptyMessage}
            </TableCell>
          </TableRow>
        ) : (
          data.map((row, index) => (
            <TableRow key={getRowKey ? getRowKey(row, index) : index} {...rowProps?.(row, index)}>
              {columns.map((col) => (
                <TableCell key={col.key} className={col.className}>
                  {col.cell(row, index)}
                </TableCell>
              ))}
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
