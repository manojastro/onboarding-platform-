import { type FormEvent, useEffect, useState } from "react";
import { type ApiErrorBody, ApiError, type Connection, type Dataset, type Policy, api, formatMiB } from "../api";
import { RejectionPanel } from "./RejectionPanel";
import { UploadSuccess } from "./UploadSuccess";

type Outcome =
  | { kind: "success"; dataset: Dataset }
  | { kind: "rejected"; status: number; filename: string; error: ApiErrorBody }
  | null;

interface Props {
  policy: Policy | null;
  connections: Connection[];
  onChange: () => void;
}

export function UploadPanel({ policy, connections, onChange }: Props) {
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [entityType, setEntityType] = useState("customer");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);

  useEffect(() => {
    if (connectionId === null && connections.length > 0) setConnectionId(connections[0].id);
  }, [connections, connectionId]);

  async function createDemoConnection() {
    await api.createConnection("Demo (mock destination)", "mock");
    onChange();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || connectionId === null) return;
    // The server enforces the limit; checking here just avoids a pointless upload.
    if (policy && file.size > policy.max_upload_bytes) {
      setOutcome({
        kind: "rejected",
        status: 413,
        filename: file.name,
        error: {
          code: "file_too_large",
          message: `${file.name} is ${formatMiB(file.size)}; the maximum upload size is ${formatMiB(policy.max_upload_bytes)}. Nothing was uploaded.`,
        },
      });
      return;
    }
    setBusy(true);
    setOutcome(null);
    try {
      setOutcome({ kind: "success", dataset: await api.uploadDataset(file, connectionId, entityType) });
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      setOutcome({ kind: "rejected", status: err.status, filename: file.name, error: err.body });
    } finally {
      setBusy(false);
      onChange();
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold">Upload a dataset</h2>
      <p className="mt-1 text-sm text-slate-600">
        CSV (UTF-8, comma-separated, header row) or a JSON array of flat objects
        {policy ? ` · max ${formatMiB(policy.max_upload_bytes)}` : ""}. A file with any structural problem is rejected
        as a whole.
      </p>

      {connections.length === 0 ? (
        <div className="mt-4 text-sm">
          <p>No source connection exists yet.</p>
          <button
            type="button"
            onClick={createDemoConnection}
            className="mt-2 rounded bg-slate-900 px-3 py-1.5 text-white hover:bg-slate-700"
          >
            Create demo connection
          </button>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-4 grid gap-4 sm:grid-cols-[1fr_1fr_2fr_auto] sm:items-end">
          <label className="text-sm">
            <span className="block font-medium">Source connection</span>
            <select
              value={connectionId ?? ""}
              onChange={(e) => setConnectionId(Number(e.target.value))}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5"
            >
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block font-medium">Entity type</span>
            <select
              value={entityType}
              onChange={(e) => setEntityType(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5"
            >
              {(policy?.entity_types ?? ["customer", "order"]).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block font-medium">File</span>
            <input
              type="file"
              accept=".csv,.json"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mt-1 block w-full text-sm file:mr-3 file:rounded file:border-0 file:bg-slate-100 file:px-3 file:py-1.5"
            />
          </label>
          <button
            type="submit"
            disabled={!file || busy}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {busy ? "Uploading…" : "Upload"}
          </button>
        </form>
      )}

      <div className="mt-4">
        {outcome?.kind === "success" && <UploadSuccess dataset={outcome.dataset} />}
        {outcome?.kind === "rejected" && (
          <RejectionPanel filename={outcome.filename} status={outcome.status} error={outcome.error} />
        )}
      </div>
    </section>
  );
}
