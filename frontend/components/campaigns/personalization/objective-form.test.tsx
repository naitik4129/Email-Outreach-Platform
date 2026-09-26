import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  EMPTY_OBJECTIVE,
  ObjectiveForm,
  validateObjective,
} from "@/components/campaigns/personalization/objective-form";
import type { PersonalizationConfig } from "@/types/domain";

const saved: PersonalizationConfig = {
  objective: "Book demos",
  offer: "Outbound automation",
  cta: "Open to a quick chat?",
  target: "SaaS founders",
  problem_solved: "",
  tone: "",
  must_mention: ["free trial"],
  never_say: [],
};

function setup(props: Partial<React.ComponentProps<typeof ObjectiveForm>> = {}) {
  const onSave = vi.fn();
  const utils = render(
    <ObjectiveForm
      initial={null}
      readOnly={false}
      saving={false}
      error={null}
      onSave={onSave}
      {...props}
    />,
  );
  return { onSave, user: userEvent.setup(), ...utils };
}

describe("validateObjective", () => {
  it("requires objective, offer and call to action", () => {
    expect(Object.keys(validateObjective(EMPTY_OBJECTIVE)).sort()).toEqual([
      "cta",
      "objective",
      "offer",
    ]);
    expect(validateObjective(saved)).toEqual({});
  });

  it("treats whitespace-only as empty and enforces length limits", () => {
    expect(validateObjective({ ...saved, offer: "   " }).offer).toBeDefined();
    expect(validateObjective({ ...saved, cta: "x".repeat(501) }).cta).toMatch(/500/);
  });
});

describe("ObjectiveForm", () => {
  it("shows what is missing and does not save an incomplete objective", async () => {
    const { onSave, user } = setup();
    await user.type(screen.getByLabelText(/^Objective/), "Book demos");
    await user.click(screen.getByRole("button", { name: /save objective/i }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getAllByText("This is required.")).toHaveLength(2); // offer + cta
  });

  it("saves a trimmed, complete objective", async () => {
    const { onSave, user } = setup();
    await user.type(screen.getByLabelText(/^Objective/), "  Book demos  ");
    await user.type(screen.getByLabelText(/offering/i), "Automation");
    await user.type(screen.getByLabelText(/call to action/i), "Chat?");
    await user.click(screen.getByRole("button", { name: /save objective/i }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({
      objective: "Book demos",
      offer: "Automation",
      cta: "Chat?",
      must_mention: [],
    });
  });

  it("keeps Save disabled until something changed", async () => {
    const { user } = setup({ initial: saved });
    const button = screen.getByRole("button", { name: /save objective/i });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText(/^Tone/), "Warm");
    expect(button).toBeEnabled();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("adds and removes must-mention phrases, rejecting duplicates", async () => {
    const { onSave, user } = setup({ initial: saved });
    const inputs = screen.getAllByPlaceholderText("Type a phrase and press Enter");
    await user.type(inputs[0], "free trial{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Already added.");
    await user.clear(inputs[0]);
    await user.type(inputs[0], "case study{Enter}");
    expect(screen.getByText("case study")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove free trial" }));
    await user.click(screen.getByRole("button", { name: /save objective/i }));
    expect(onSave.mock.calls[0][0].must_mention).toEqual(["case study"]);
  });

  it("limits a phrase list to ten entries", async () => {
    const many = { ...saved, never_say: Array.from({ length: 10 }, (_, i) => `p${i}`) };
    const { user } = setup({ initial: many });
    const inputs = screen.getAllByPlaceholderText("Type a phrase and press Enter");
    await user.type(inputs[1], "one more{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent(/up to 10/);
  });

  it("is read-only when the campaign is no longer a draft", () => {
    setup({ initial: saved, readOnly: true });
    expect(screen.getByLabelText(/^Objective/)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /save objective/i })).not.toBeInTheDocument();
    expect(screen.getByText(/can't be edited now/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();
  });

  it("shows a server error and a saving state", () => {
    setup({ initial: saved, error: "The objective was modified by another user.", saving: true });
    expect(screen.getByRole("alert")).toHaveTextContent(/modified by another user/);
    expect(screen.getByRole("button", { name: /save objective/i })).toBeDisabled();
  });

  it("does not overwrite unsaved edits when the server copy refreshes", async () => {
    const { user, rerender, onSave } = setup({ initial: saved });
    await user.type(screen.getByLabelText(/^Tone/), "Warm");
    rerender(
      <ObjectiveForm
        initial={{ ...saved, target: "Changed elsewhere" }}
        readOnly={false}
        saving={false}
        error={null}
        onSave={onSave}
      />,
    );
    expect(screen.getByLabelText(/^Tone/)).toHaveValue("Warm");
  });

  it("follows the server copy when there are no local edits", () => {
    const { rerender, onSave } = setup({ initial: saved });
    rerender(
      <ObjectiveForm
        initial={{ ...saved, offer: "Updated offer" }}
        readOnly={false}
        saving={false}
        error={null}
        onSave={onSave}
      />,
    );
    expect(screen.getByLabelText(/offering/i)).toHaveValue("Updated offer");
  });
});
