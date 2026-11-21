// Phase 1 acceptance, UI side: a malformed CSV produces a clear, row-level
// error in the UI, and the UI says nothing was stored.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Connection, Policy } from "../api";
import { UploadPanel } from "./UploadPanel";

const policy: Policy = {
  max_upload_bytes: 10 * 1024 * 1024,
  entity_types: ["customer", "order"],
  destination_types: ["mock", "stripe"],
  allowed_file_extensions: [".csv", ".json"],
  currencies: { USD: 2 },
  date_formats: [],
};
const connections: Connection[] = [{ id: 1, name: "Demo (mock destination)", destination_type: "mock", created_at: "" }];

// The exact envelope the backend returns for MALFORMED_CSV in test_upload_api.py.
const rejection = {
  error: {
    code: "parse_failed",
    message: "'customers.csv' was rejected: 3 problems found. Nothing was stored.",
    error_count: 3,
    errors_truncated: false,
    errors: [
      { code: "field_count_mismatch", message: "Row 2 (line 3) has 4 fields but the header has 3.", line_number: 3, row_number: 2, column: null },
      { code: "field_count_mismatch", message: "Row 3 (line 4) has 2 fields but the header has 3.", line_number: 4, row_number: 3, column: null },
      { code: "malformed_quoting", message: "Line 5: a quoted value is never closed.", line_number: 5, row_number: 4, column: null },
    ],
  },
};

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: status < 400, status, text: async () => JSON.stringify(body) });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("UploadPanel", () => {
  it("shows every row-level parse error and says nothing was stored", async () => {
    mockFetch(422, rejection);
    const user = userEvent.setup();
    render(<UploadPanel policy={policy} connections={connections} onChange={() => {}} />);

    await user.upload(screen.getByLabelText("File"), new File(["id\n"], "customers.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("heading")).toHaveTextContent("customers.csv was rejected — nothing was stored");
    expect(alert).toHaveTextContent("3 problems found. Nothing was stored.");

    const rows = within(alert).getAllByRole("row").slice(1); // skip the header row
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Wrong number of fields");
    expect(within(rows[0]).getAllByRole("cell")[0]).toHaveTextContent("3"); // line
    expect(within(rows[0]).getAllByRole("cell")[1]).toHaveTextContent("2"); // row
    expect(rows[2]).toHaveTextContent("Broken quoting");
    expect(rows[2]).toHaveTextContent("quoted value is never closed");
  });

  it("refuses an oversized file without sending it", async () => {
    const fetchMock = mockFetch(201, {});
    const user = userEvent.setup();
    render(<UploadPanel policy={{ ...policy, max_upload_bytes: 4 }} connections={connections} onChange={() => {}} />);

    await user.upload(screen.getByLabelText("File"), new File(["0123456789"], "big.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was uploaded");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the server message even when a proxy returns a non-JSON 413", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 413, text: async () => "<html>413</html>" }));
    const user = userEvent.setup();
    render(<UploadPanel policy={policy} connections={connections} onChange={() => {}} />);

    await user.upload(screen.getByLabelText("File"), new File(["id\n"], "c.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("larger than the upload limit. Nothing was stored.");
  });
});
