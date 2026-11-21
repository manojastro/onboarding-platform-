import type { AuditEvent } from "../api";

function summary(event: AuditEvent): string {
  const message = event.detail["message"];
  if (typeof message === "string") return message;
  const filename = event.detail["filename"];
  if (typeof filename === "string") return filename;
  const name = event.detail["name"];
  return typeof name === "string" ? name : "";
}

export function AuditLog({ events }: { events: AuditEvent[] }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold">Audit log</h2>
      <p className="mt-1 text-sm text-slate-600">Append-only. Rejected uploads are recorded here even though nothing was stored.</p>
      {events.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No events yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100 text-sm">
          {events.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 py-2">
              <span className="w-40 shrink-0 whitespace-nowrap text-xs text-slate-500">
                {new Date(e.created_at).toLocaleString()}
              </span>
              <code
                className={
                  e.event_type.endsWith("rejected") || e.event_type.includes("suspicious")
                    ? "text-red-700"
                    : "text-slate-800"
                }
              >
                {e.event_type}
              </code>
              <span className="text-slate-500">
                {e.subject_type}
                {e.subject_id ? ` #${e.subject_id}` : ""} · {e.actor}
              </span>
              <span className="min-w-0 flex-1 truncate text-slate-700">{summary(e)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
