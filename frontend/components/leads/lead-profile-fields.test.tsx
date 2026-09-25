import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { emptyProfileValues, profilePayload } from "@/lib/lead-fields";

import { LeadProfileDetails } from "./lead-profile-fields";

const blank = profilePayload(emptyProfileValues);

describe("LeadProfileDetails", () => {
  it("says so when a lead has no extra details", () => {
    render(<LeadProfileDetails lead={blank} />);

    expect(screen.getByText("No additional details.")).toBeInTheDocument();
  });

  it("shows only the fields and groups that have values", () => {
    render(
      <LeadProfileDetails
        lead={{ ...blank, phone: "+1 555 123 4567", experience_years: 0, city: "Berlin" }}
      />,
    );

    expect(screen.getByText("Professional")).toBeInTheDocument();
    expect(screen.getByText("Location")).toBeInTheDocument();
    expect(screen.queryByText("Company")).not.toBeInTheDocument();
    expect(screen.getByText("+1 555 123 4567")).toBeInTheDocument();
    // Zero is a real value, not "empty".
    expect(screen.getByText("Experience (years)")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryByText("Department")).not.toBeInTheDocument();
  });

  it("links http(s) URLs safely", () => {
    render(<LeadProfileDetails lead={{ ...blank, website: "https://example.com/a" }} />);

    const link = screen.getByRole("link", { name: "https://example.com/a" });
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("never renders a non-http(s) value as a link", () => {
    render(<LeadProfileDetails lead={{ ...blank, website: "javascript:alert(1)" }} />);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
  });
});
