import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/mailboxes/connect/smtp",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { connectSmtpMailbox } = vi.hoisted(() => ({ connectSmtpMailbox: vi.fn() }));

vi.mock("@/lib/mailboxes-api", () => ({ connectSmtpMailbox }));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { SmtpConnectClient } from "./smtp-connect-client";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/host/i), "smtp.example.com");
  await user.type(screen.getByLabelText(/username/i), "user@example.com");
  await user.type(screen.getByLabelText(/^password/i), "s3cret-password");
  await user.type(screen.getByLabelText(/sender email address/i), "sales@example.com");
}

describe("SmtpConnectClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a client-side validation error and never calls the API when the form is incomplete", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    const user = userEvent.setup();
    renderWithClient(<SmtpConnectClient />);

    await user.click(screen.getByRole("button", { name: /validate & connect/i }));

    expect(await screen.findByText(/host is required/i)).toBeInTheDocument();
    expect(connectSmtpMailbox).not.toHaveBeenCalled();
  });

  it("submits the configuration and redirects to the mailbox on success", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    connectSmtpMailbox.mockResolvedValue({
      mailbox_id: "mb-99",
      provider: "SMTP",
      email_address: "sales@example.com",
      connection_state: "CONNECTED",
      health_state: "HEALTHY",
    });
    const user = userEvent.setup();
    renderWithClient(<SmtpConnectClient />);

    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: /validate & connect/i }));

    expect(connectSmtpMailbox).toHaveBeenCalledWith("ws-1", {
      host: "smtp.example.com",
      port: 587,
      security_mode: "STARTTLS",
      username: "user@example.com",
      password: "s3cret-password",
      email_address: "sales@example.com",
      sender_display_name: null,
    });
    expect(await screen.findByLabelText(/^password/i)).toHaveValue("");
  });

  it("maps a backend error code to a safe message instead of showing raw backend text", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    const { ApiError } = await import("@/lib/api-client");
    connectSmtpMailbox.mockRejectedValue(
      new ApiError(
        "connect ECONNREFUSED 10.0.0.5:587 (internal detail)",
        422,
        "unsafe_destination",
        null,
      ),
    );
    const user = userEvent.setup();
    renderWithClient(<SmtpConnectClient />);

    await fillValidForm(user);
    await user.click(screen.getByRole("button", { name: /validate & connect/i }));

    expect(
      await screen.findByText(/that server address is not allowed/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/10\.0\.0\.5/)).not.toBeInTheDocument();
  });

  it("updates the default port when the security mode changes", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    const user = userEvent.setup();
    renderWithClient(<SmtpConnectClient />);

    const portInput = screen.getByLabelText(/port/i) as HTMLInputElement;
    expect(portInput.value).toBe("587");

    await user.selectOptions(screen.getByLabelText(/security mode/i), "IMPLICIT_TLS");
    expect(portInput.value).toBe("465");
  });

  it("sends the optional IMAP settings so replies can be detected", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    connectSmtpMailbox.mockResolvedValue({
      mailbox_id: "mb-99",
      provider: "SMTP",
      email_address: "sales@example.com",
      connection_state: "CONNECTED",
      health_state: "HEALTHY",
    });
    const user = userEvent.setup();
    renderWithClient(<SmtpConnectClient />);

    await user.type(screen.getByLabelText(/^host/i), "smtp.example.com");
    await user.type(screen.getByLabelText(/^username/i), "user@example.com");
    await user.type(screen.getByLabelText(/^password/i), "s3cret-password");
    await user.type(screen.getByLabelText(/sender email address/i), "sales@example.com");
    await user.type(screen.getByLabelText(/incoming mail server/i), "imap.example.com");
    await user.click(screen.getByRole("button", { name: /validate & connect/i }));

    expect(connectSmtpMailbox).toHaveBeenCalledWith(
      "ws-1",
      expect.objectContaining({
        imap_host: "imap.example.com",
        imap_port: 993,
        imap_security_mode: "IMPLICIT_TLS",
      }),
    );
    // Not entered -> not sent: the IMAP login falls back to the SMTP login.
    const sent = connectSmtpMailbox.mock.calls[0][1];
    expect(sent).not.toHaveProperty("imap_username");
    expect(sent).not.toHaveProperty("imap_password");
  });

  it("does not send any IMAP field when none was entered (send-only mailbox)", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    connectSmtpMailbox.mockResolvedValue({
      mailbox_id: "mb-99",
      provider: "SMTP",
      email_address: "sales@example.com",
      connection_state: "CONNECTED",
      health_state: "HEALTHY",
    });
    const user = userEvent.setup();
    renderWithClient(<SmtpConnectClient />);
    await user.type(screen.getByLabelText(/^host/i), "smtp.example.com");
    await user.type(screen.getByLabelText(/^username/i), "user@example.com");
    await user.type(screen.getByLabelText(/^password/i), "s3cret-password");
    await user.type(screen.getByLabelText(/sender email address/i), "sales@example.com");
    await user.click(screen.getByRole("button", { name: /validate & connect/i }));

    const sent = connectSmtpMailbox.mock.calls[0][1];
    expect(Object.keys(sent).some((key) => key.startsWith("imap_"))).toBe(false);
  });
});
