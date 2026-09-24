import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

let mockToken: string | null = "valid-token-abc";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  useSearchParams: () => ({
    get: (key: string) => (key === "token" ? mockToken : null),
  }),
}));

const mockTeamApi = vi.hoisted(() => ({
  verifyInvitation: vi.fn(),
  acceptInvitation: vi.fn(),
}));

vi.mock("@/lib/team-api", () => mockTeamApi);

import AcceptInvitePage from "@/app/auth/accept-invite/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AcceptInvitePage />
    </QueryClientProvider>,
  );
}

describe("AcceptInvitePage", () => {
  const user = userEvent.setup();

  beforeEach(() => {
    mockToken = "valid-token-abc";
    mockTeamApi.verifyInvitation.mockResolvedValue({
      valid: true,
      email: "invitee@example.com",
      workspace_name: "Stark Industries",
      role_code: "MEMBER",
      expires_at: "2026-10-01T00:00:00Z",
    });
    mockTeamApi.acceptInvitation.mockResolvedValue({
      workspace_id: "ws-stark",
      membership_id: "m-stark",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows invalid link error when token is absent", async () => {
    mockToken = null;
    renderPage();

    expect(await screen.findByText("Invalid Link")).toBeInTheDocument();
  });

  it("verifies and displays invitation details and accepts", async () => {
    renderPage();

    expect(await screen.findByText("Stark Industries")).toBeInTheDocument();
    expect(screen.getByText("invitee@example.com")).toBeInTheDocument();
    expect(screen.getByText("MEMBER")).toBeInTheDocument();

    const acceptBtn = screen.getByRole("button", {
      name: /accept invitation & join/i,
    });
    await user.click(acceptBtn);

    await waitFor(() => {
      expect(mockTeamApi.acceptInvitation).toHaveBeenCalledWith("valid-token-abc");
    });

    expect(await screen.findByText(/invitation accepted/i)).toBeInTheDocument();
  });
});
