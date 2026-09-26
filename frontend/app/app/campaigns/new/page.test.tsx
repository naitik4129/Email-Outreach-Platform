import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const campaignsApi = vi.hoisted(() => ({ createCampaign: vi.fn() }));
vi.mock("@/lib/campaigns-api", () => campaignsApi);

const personalizationApi = vi.hoisted(() => ({ getPersonalizationCapabilities: vi.fn() }));
vi.mock("@/lib/personalization-api", () => personalizationApi);

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import NewCampaignPage from "./page";

function setup(
  role = "MEMBER",
  capabilities: { enabled: boolean; model: string | null } = { enabled: true, model: "m" },
) {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-1",
    activeWorkspace: { role_code: role },
  });
  personalizationApi.getPersonalizationCapabilities.mockResolvedValue(capabilities);
  campaignsApi.createCampaign.mockResolvedValue({ id: "camp-9" });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <NewCampaignPage />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

describe("New campaign type selector", () => {
  afterEach(() => vi.clearAllMocks());

  it("creates a standard campaign by default", async () => {
    const user = setup();
    await user.type(screen.getByLabelText(/campaign name/i), "Q1 Outbound");
    await user.click(screen.getByRole("button", { name: "Create Campaign" }));
    await waitFor(() => expect(campaignsApi.createCampaign).toHaveBeenCalled());
    expect(campaignsApi.createCampaign.mock.calls[0][1]).toMatchObject({
      name: "Q1 Outbound",
      campaign_type: "STANDARD",
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/app/campaigns/camp-9/audience"));
  });

  it("creates a hyper-personalized campaign when enabled and chosen", async () => {
    const user = setup();
    const hyper = await screen.findByRole("radio", { name: /hyper-personalized/i });
    await waitFor(() => expect(hyper).toBeEnabled());
    await user.click(hyper);
    await user.type(screen.getByLabelText(/campaign name/i), "Founders");
    await user.click(screen.getByRole("button", { name: "Create Campaign" }));
    await waitFor(() => expect(campaignsApi.createCampaign).toHaveBeenCalled());
    expect(campaignsApi.createCampaign.mock.calls[0][1].campaign_type).toBe("HYPER_PERSONALIZED");
  });

  it("disables the hyper-personalized option when the deployment has it off", async () => {
    setup("MEMBER", { enabled: false, model: null });
    const hyper = await screen.findByRole("radio", { name: /hyper-personalized/i });
    await waitFor(() =>
      expect(screen.getByText("Not enabled for this deployment.")).toBeInTheDocument(),
    );
    expect(hyper).toBeDisabled();
    expect(screen.getByRole("radio", { name: /standard/i })).toBeChecked();
  });

  it("keeps the option unavailable while availability is unknown", async () => {
    personalizationApi.getPersonalizationCapabilities.mockReturnValue(new Promise(() => {}));
    useWorkspace.mockReturnValue({
      activeWorkspaceId: "ws-1",
      activeWorkspace: { role_code: "MEMBER" },
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <NewCampaignPage />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("radio", { name: /hyper-personalized/i })).toBeDisabled();
    expect(screen.getByText("Checking availability…")).toBeInTheDocument();
  });

  it("tells the user the type is permanent", () => {
    setup();
    expect(screen.getByText(/can't be changed after creation/i)).toBeInTheDocument();
  });

  it("denies people without campaign-draft permission", () => {
    setup("VIEWER");
    expect(screen.getByText(/do not have permission to create campaigns/i)).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});
