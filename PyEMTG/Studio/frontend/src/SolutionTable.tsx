import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table'
import { useMemo } from 'react'
import type { Solution } from './types'

const format = (value: unknown, digits = 2) => typeof value === 'number' ? value.toFixed(digits) : (value ?? '—')

export function SolutionTable({
  solutions, selected, onToggle,
}: { solutions: Solution[]; selected: Set<string>; onToggle: (solution: Solution) => void }) {
  const columns = useMemo<ColumnDef<Solution>[]>(() => [
    { id: 'select', header: '', cell: ({ row }) => <input aria-label="Compare solution" type="checkbox" checked={selected.has(row.original.id)} onChange={() => onToggle(row.original)} /> },
    { accessorKey: 'pareto', header: 'P', cell: ({ getValue }) => getValue() ? '◆' : '' },
    { accessorKey: 'sequence_text', header: 'Route' },
    { accessorKey: 'launch_mjd', header: 'Launch MJD', cell: ({ getValue }) => format(getValue(), 3) },
    { accessorKey: 'flight_time_days', header: 'Time d', cell: ({ getValue }) => format(getValue()) },
    { accessorKey: 'propellant_used_kg', header: 'Prop kg', cell: ({ getValue }) => format(getValue()) },
    { accessorKey: 'delivered_mass_kg', header: 'Mass kg', cell: ({ getValue }) => format(getValue()) },
    { accessorKey: 'thrust_max_n', header: 'Max thrust N', cell: ({ getValue }) => format(getValue(), 4) },
    { accessorKey: 'fidelity', header: 'Fidelity' },
    { accessorKey: 'generation', header: 'Gen' },
  ], [selected, onToggle])
  const table = useReactTable({ data: solutions, columns, getCoreRowModel: getCoreRowModel() })
  return <div className="solution-table-wrap">
    <table className="solution-table">
      <thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead>
      <tbody>{table.getRowModel().rows.map(row => <tr key={row.id} className={selected.has(row.original.id) ? 'selected-row' : ''} onDoubleClick={() => onToggle(row.original)}>{row.getVisibleCells().map(cell => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
    </table>
  </div>
}
