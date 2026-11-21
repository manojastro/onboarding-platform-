import { useCallback, useEffect, useState } from "react";
import { ApiError, type AuditEvent, type Connection, type Dataset, type Policy, api } from "./api";
import { AuditLog } from "./components/AuditLog";
import { DatasetTable } from "./components/DatasetTable";
import { UploadPanel } from "./components/UploadPanel";

export default function App() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [p, c, d, a] = await Promise.all([api.policy(), api.connections(), api.datasets(), api.auditEvents()]);
      setPolicy(p);
      setConnections(c);
      setDatasets(d);
      setAuditEvents(a);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-baseline justify-between gap-2 px-4 py-4">
          <h1 className="text-lg font-semibold">Onboarding Platform</h1>
          <span className="text-xs text-slate-500">
            Single operator · deterministic code decides · the LLM only suggests
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        {loadError && (
          <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">
            {loadError}
          </div>
        )}
        <UploadPanel policy={policy} connections={connections} onChange={refresh} />
        <DatasetTable datasets={datasets} connections={connections} />
        <AuditLog events={auditEvents} />
      </main>
    </div>
  );
}
