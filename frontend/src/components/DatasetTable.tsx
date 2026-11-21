import type { Connection, Dataset } from "../api";

interface Props {
  datasets: Dataset[];
  connections: Connection[];
}

export function DatasetTable({ datasets, connections }: Props) {
  const connectionName = (id: number) => connections.find((c) => c.id === id)?.name ?? `#${id}`;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold">Datasets</h2>
      {datasets.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No datasets yet. Only files that parse cleanly appear here.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="py-2 pr-4">#</th>
                <th className="py-2 pr-4">File</th>
                <th className="py-2 pr-4">Entity</th>
                <th className="py-2 pr-4">Connection</th>
                <th className="py-2 pr-4 text-right">Rows</th>
                <th className="py-2 pr-4 text-right">Columns</th>
                <th className="py-2 pr-4">Schema fingerprint</th>
                <th className="py-2 pr-4">Uploaded</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {datasets.map((d) => (
                <tr key={d.id}>
                  <td className="py-2 pr-4 font-mono">{d.id}</td>
                  <td className="py-2 pr-4">{d.original_filename}</td>
                  <td className="py-2 pr-4">{d.entity_type}</td>
                  <td className="py-2 pr-4">{connectionName(d.source_connection_id)}</td>
                  <td className="py-2 pr-4 text-right font-mono">{d.row_count.toLocaleString()}</td>
                  <td className="py-2 pr-4 text-right font-mono">{d.columns.length}</td>
                  <td className="py-2 pr-4 font-mono text-xs" title={d.schema_fingerprint}>
                    {d.schema_fingerprint.slice(0, 17)}…
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">{new Date(d.uploaded_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
