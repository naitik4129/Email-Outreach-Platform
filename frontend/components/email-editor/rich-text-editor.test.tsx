import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { EmailBodyEditor, type EditorMode } from "@/components/email-editor/rich-text-editor";

function Harness({
  initial = "<p>Hello</p>",
  initialMode = "visual",
  onValue,
  onLossy,
}: {
  initial?: string;
  initialMode?: EditorMode;
  onValue?: (html: string) => void;
  onLossy?: () => void;
}) {
  const [value, setValue] = React.useState(initial);
  const [mode, setMode] = React.useState<EditorMode>(initialMode);
  return (
    <>
      <EmailBodyEditor
        value={value}
        onChange={(html) => {
          setValue(html);
          onValue?.(html);
        }}
        mode={mode}
        onModeChange={setMode}
        onLossy={onLossy}
      />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("EmailBodyEditor", () => {
  it("renders the toolbar with every formatting control", async () => {
    render(<Harness />);
    await screen.findByRole("toolbar", { name: /email formatting/i });
    for (const name of [
      /^font$/i,
      /font size/i,
      /^bold$/i,
      /^italic$/i,
      /^underline$/i,
      /text color/i,
      /clear formatting/i,
      /align left/i,
      /align center/i,
      /align right/i,
      /numbered list/i,
      /bulleted list/i,
      /^link$/i,
      /edit html source/i,
      /more formatting/i,
      /insert variable in body/i,
    ]) {
      expect(screen.getAllByLabelText(name).length).toBeGreaterThan(0);
    }
  });

  it("never rewrites the stored HTML just by mounting", async () => {
    const onValue = vi.fn();
    render(<Harness initial="<p>Hi   there </p><p></p>" onValue={onValue} />);
    await screen.findByRole("toolbar", { name: /email formatting/i });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(onValue).not.toHaveBeenCalled();
    expect(screen.getByTestId("value")).toHaveTextContent("<p>Hi there </p><p></p>");
  });

  it("switches to HTML source, edits it, and returns to the visual editor", async () => {
    const user = userEvent.setup();
    render(<Harness initial="<p>Hello</p>" />);
    await screen.findByRole("toolbar", { name: /email formatting/i });

    await user.click(screen.getByRole("button", { name: /edit html source/i }));
    const textarea = await screen.findByLabelText("HTML source");
    expect(textarea).toHaveValue("<p>Hello</p>");

    await user.clear(textarea);
    await user.click(textarea);
    await user.paste("<p>Edited <strong>bold</strong></p>");
    expect(screen.getByTestId("value")).toHaveTextContent("<p>Edited <strong>bold</strong></p>");

    await user.click(screen.getByRole("button", { name: /switch to visual editor/i }));
    await waitFor(() => {
      expect(document.querySelector(".ProseMirror")).toHaveTextContent("Edited bold");
    });
  });

  it("inserts a variable at the cursor in source mode", async () => {
    const user = userEvent.setup();
    render(<Harness initial="<p>Hi </p>" initialMode="source" />);
    const textarea = await screen.findByLabelText("HTML source");
    // Caret to just before "</p>": click puts it at the end, then step back 4.
    await user.click(textarea);
    await user.keyboard("{End}{ArrowLeft}{ArrowLeft}{ArrowLeft}{ArrowLeft}");

    await user.click(screen.getByRole("button", { name: /insert variable in body/i }));
    await user.click(await screen.findByRole("button", { name: "First name" }));

    expect(screen.getByTestId("value")).toHaveTextContent("<p>Hi {{first_name}}</p>");
  });

  it("adds a fallback to an inserted variable", async () => {
    const user = userEvent.setup();
    render(<Harness initial="" initialMode="source" />);
    await screen.findByLabelText("HTML source");

    await user.click(screen.getByRole("button", { name: /insert variable in body/i }));
    await user.type(await screen.findByLabelText(/fallback if empty/i), "there");
    await user.click(screen.getByRole("button", { name: "First name" }));

    expect(screen.getByTestId("value")).toHaveTextContent("{{first_name|there}}");
  });

  it("warns when leaving source mode drops markup the visual editor can't keep", async () => {
    const user = userEvent.setup();
    const onLossy = vi.fn();
    render(
      <Harness
        initial="<table><tr><td>cell</td></tr></table><p>after</p>"
        initialMode="source"
        onLossy={onLossy}
      />,
    );
    await screen.findByLabelText("HTML source");
    await user.click(screen.getByRole("button", { name: /switch to visual editor/i }));
    await waitFor(() => expect(onLossy).toHaveBeenCalled());
  });
});
