import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TypeToConfirmDialog } from "@/components/ui/type-to-confirm-dialog";

function setup(props: Partial<React.ComponentProps<typeof TypeToConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <TypeToConfirmDialog
      open
      title="Delete it?"
      description="Gone for good."
      phrase="Acme Outbound"
      confirmLabel="Delete permanently"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onConfirm, onCancel };
}

describe("TypeToConfirmDialog", () => {
  it("keeps the action disabled until the exact phrase is typed", async () => {
    const user = userEvent.setup();
    const { onConfirm } = setup();
    const confirm = await screen.findByRole("button", { name: "Delete permanently" });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByRole("textbox"), "Acme");
    expect(confirm).toBeDisabled();
    await user.type(screen.getByRole("textbox"), " Outbound");
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("is case sensitive and ignores surrounding spaces", async () => {
    const user = userEvent.setup();
    setup();
    const confirm = await screen.findByRole("button", { name: "Delete permanently" });
    await user.type(screen.getByRole("textbox"), "acme outbound");
    expect(confirm).toBeDisabled();
    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "  Acme Outbound  ");
    expect(confirm).toBeEnabled();
  });

  it("does not submit on Enter until the phrase matches", async () => {
    const user = userEvent.setup();
    const { onConfirm } = setup();
    await user.type(await screen.findByRole("textbox"), "nope{Enter}");
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("shows the server's refusal and cancels", async () => {
    const user = userEvent.setup();
    const { onCancel } = setup({ error: "This template was used in a campaign." });
    expect(await screen.findByRole("alert")).toHaveTextContent("used in a campaign");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("clears what was typed when it is reopened", async () => {
    const user = userEvent.setup();
    const props = {
      title: "Delete it?",
      description: "x",
      phrase: "Acme",
      confirmLabel: "Delete",
      onConfirm: vi.fn(),
      onCancel: vi.fn(),
    };
    const { rerender } = render(<TypeToConfirmDialog open {...props} />);
    await user.type(await screen.findByRole("textbox"), "Acme");
    rerender(<TypeToConfirmDialog open={false} {...props} />);
    rerender(<TypeToConfirmDialog open {...props} />);
    expect(await screen.findByRole("textbox")).toHaveValue("");
  });
});
