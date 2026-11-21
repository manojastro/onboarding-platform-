// Typed client for the backend. Every failure becomes an ApiError carrying the
// backend's error envelope, so the UI can always show the server's own message.

export interface ParseIssue {
  code: string;
  message: string;
  line_number: number | null;
  row_number: number | null;
  column: string | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  errors?: ParseIssue[];
  error_count?: number;
  errors_truncated?: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
}

export interface Connection {
  id: number;
  name: string;
  destination_type: string;
  created_at: string;
}

export interface Dataset {
  id: number;
  source_connection_id: number;
  entity_type: string;
  original_filename: string;
  file_format: string;
  file_size_bytes: number;
  file_sha256: string;
  schema_fingerprint: string;
  columns: string[];
  row_count: number;
  warnings: ParseIssue[];
  uploaded_at: string;
}

export interface DatasetDetail extends Dataset {
  preview_rows: Record<string, string | null>[];
}

export interface Policy {
  max_upload_bytes: number;
  entity_types: string[];
  destination_types: string[];
  allowed_file_extensions: string[];
  currencies: Record<string, number>;
  date_formats: { name: string; shape: string; example: string }[];
}

export interface AuditEvent {
  id: number;
  actor: string;
  event_type: string;
  subject_type: string;
  subject_id: string | null;
  detail: Record<string, unknown>;
  created_at: string;
}

function fallbackMessage(status: number): string {
  if (status === 413) return "The file is larger than the upload limit. Nothing was stored.";
  return `The server returned HTTP ${status}.`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError(0, { code: "network_error", message: "Could not reach the API. Is the backend running?" });
  }
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null; // e.g. an HTML error page from a proxy
  }
  if (!response.ok) {
    const body = (payload as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(response.status, body ?? { code: `http_${response.status}`, message: fallbackMessage(response.status) });
  }
  return payload as T;
}

export const api = {
  policy: () => request<Policy>("/policy"),
  connections: () => request<Connection[]>("/connections"),
  createConnection: (name: string, destinationType: string) =>
    request<Connection>("/connections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, destination_type: destinationType }),
    }),
  datasets: () => request<Dataset[]>("/datasets"),
  dataset: (id: number) => request<DatasetDetail>(`/datasets/${id}`),
  uploadDataset: (file: File, connectionId: number, entityType: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("source_connection_id", String(connectionId));
    form.append("entity_type", entityType);
    return request<Dataset>("/datasets", { method: "POST", body: form });
  },
  auditEvents: (limit = 20) => request<AuditEvent[]>(`/audit-events?limit=${limit}`),
};

export function formatMiB(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}
