import type { Dataset } from "../api";

export function UploadSuccess({ dataset }: { dataset: Dataset }) {
  const suspicious = dataset.warnings.filter((w) => w.code === "suspicious_instruction_text");
  const other = dataset.warnings.filter((w) => w.code !== "suspicious_instruction_text");

  return (
    <section aria-labelledby="upload-success-title" className="rounded-lg border border-emerald-300 bg-emerald-50 p-4">
      <h3 id="upload-success-title" className="font-semibold text-emerald-900">
        Dataset #{dataset.id} created from {dataset.original_filename}
      </h3>
      <p className="mt-1 text-sm text-emerald-900">
        {dataset.row_count.toLocaleString()} rows × {dataset.columns.length} columns · {dataset.entity_type} ·{" "}
        <span className="font-mono text-xs">{dataset.schema_fingerprint.slice(0, 17)}…</span>
      </p>

      {suspicious.length > 0 && (
        <div className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">
            {suspicious.length} cell{suspicious.length === 1 ? "" : "s"} contain text that looks like instructions to an AI
            system.
          </p>
          <p className="mt-1">The text is kept verbatim as data and logged. No action was taken on it.</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {suspicious.slice(0, 5).map((w, i) => (
              <li key={i}>{w.message}</li>
            ))}
          </ul>
        </div>
      )}

      {other.length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">
          {other.map((w, i) => (
            <li key={i}>{w.message}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
