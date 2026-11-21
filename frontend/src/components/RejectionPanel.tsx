import type { ApiErrorBody } from "../api";

const PROBLEM_LABELS: Record<string, string> = {
  field_count_mismatch: "Wrong number of fields",
  malformed_quoting: "Broken quoting",
  field_too_large: "Value too long",
  invalid_encoding: "Not UTF-8",
  nul_byte: "Binary content",
  empty_file: "Empty file",
  missing_header: "No header row",
  blank_header: "Unnamed column",
  duplicate_header: "Duplicate column",
  too_many_columns: "Too many columns",
  too_many_rows: "Too many rows",
  no_data_rows: "No data rows",
  invalid_json: "Invalid JSON",
  json_not_array: "Not a JSON array",
  row_not_object: "Record is not an object",
  nested_value: "Nested value",
  duplicate_key: "Duplicate key",
  blank_key: "Empty key",
};

interface Props {
  filename: string;
  status: number;
  error: ApiErrorBody;
}

export function RejectionPanel({ filename, status, error }: Props) {
  const problems = error.errors ?? [];
  const total = error.error_count ?? problems.length;

  return (
    <section role="alert" aria-labelledby="rejection-title" className="rounded-lg border border-red-300 bg-red-50 p-4">
      <h3 id="rejection-title" className="font-semibold text-red-900">
        {filename} was rejected — nothing was stored
      </h3>
      <p className="mt-1 text-sm text-red-900">{error.message}</p>
      <p className="mt-1 text-xs text-red-700">
        HTTP {status} · <code>{error.code}</code>
      </p>

      {problems.length > 0 && (
        <>
          <div className="mt-3 overflow-x-auto rounded border border-red-200 bg-white">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-red-100 text-xs uppercase tracking-wide text-red-900">
                <tr>
                  <th className="px-3 py-2">Line</th>
                  <th className="px-3 py-2">Row</th>
                  <th className="px-3 py-2">Column</th>
                  <th className="px-3 py-2">Problem</th>
                  <th className="px-3 py-2">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-red-100">
                {problems.map((p, i) => (
                  <tr key={i} className="align-top">
                    <td className="px-3 py-2 font-mono">{p.line_number ?? "—"}</td>
                    <td className="px-3 py-2 font-mono">{p.row_number ?? "—"}</td>
                    <td className="px-3 py-2 font-mono">{p.column ?? "—"}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {PROBLEM_LABELS[p.code] ?? p.code}
                      <div className="font-mono text-xs text-slate-500">{p.code}</div>
                    </td>
                    <td className="px-3 py-2">{p.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {error.errors_truncated && (
            <p className="mt-2 text-xs text-red-800">
              Showing the first {problems.length} of {total} problems.
            </p>
          )}
        </>
      )}
    </section>
  );
}
