import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { CampaignStatusBadge } from "@/components/ui/status-badge";

describe("CampaignStatusBadge", () => {
  it("renders the raw status code as text so status is never color-only", () => {
    render(<CampaignStatusBadge status="RUNNING" />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
  });

  it("tolerates a status this build does not know", () => {
    // @ts-expect-error simulating a newer server status
    render(<CampaignStatusBadge status="QUARANTINED" />);
    expect(screen.getByText("QUARANTINED")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("does not render while closed", () => {
    render(
      <ConfirmDialog open={false} title="Archive?" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("confirms and cancels through explicit buttons", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Archive campaign?"
        description="It will no longer be editable."
        confirmLabel="Archive campaign"
        tone="danger"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );

    expect(await screen.findByRole("dialog", { name: "Archive campaign?" })).toBeInTheDocument();
    expect(screen.getByText("It will no longer be editable.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Archive campaign" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  // Callers mount the dialog closed and flip it open, so the tests do too.
  function openDialog(props: Partial<React.ComponentProps<typeof ConfirmDialog>>) {
    const onCancel = props.onCancel ?? vi.fn();
    const base = { title: "Pause?", onConfirm: vi.fn(), onCancel };
    const view = render(<ConfirmDialog open={false} {...base} {...props} />);
    view.rerender(<ConfirmDialog {...base} {...props} open />);
    return onCancel;
  }

  it("cancels on Escape", async () => {
    const user = userEvent.setup();
    const onCancel = openDialog({});

    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("cannot be dismissed while the action is in flight", async () => {
    const user = userEvent.setup();
    const onCancel = openDialog({ loading: true });

    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(onCancel).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
  });
});

describe("DropdownMenu", () => {
  it("opens, runs an item, and closes", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <DropdownMenu
        label="Actions"
        items={[
          { label: "Duplicate", onSelect },
          { label: "Archive", onSelect: vi.fn(), tone: "danger" },
        ]}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Actions" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("moves focus with arrow keys and closes on Escape, returning focus to the trigger", async () => {
    const user = userEvent.setup();
    render(
      <DropdownMenu
        label="Actions"
        items={[
          { label: "One", onSelect: vi.fn() },
          { label: "Two", onSelect: vi.fn() },
        ]}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Actions" });
    await user.click(trigger);
    expect(screen.getByRole("menuitem", { name: "One" })).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Two" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("does not run a disabled item", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <DropdownMenu label="Actions" items={[{ label: "Duplicate", onSelect, disabled: true }]} />,
    );

    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
