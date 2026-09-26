import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import {
  IMAP_DEFAULTS,
  ImapSettingsFields,
  imapPayload,
  imapSecurityModeForPort,
  type ImapSettings,
} from "./imap-settings-fields";

function Harness({ onValue }: { onValue: (value: ImapSettings) => void }) {
  const [value, setValue] = useState<ImapSettings>(IMAP_DEFAULTS);
  return (
    <ImapSettingsFields
      idPrefix="t"
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue(next);
      }}
    />
  );
}

describe("imapPayload", () => {
  it("sends nothing when no IMAP host was entered (send-only mailbox)", () => {
    expect(imapPayload(IMAP_DEFAULTS)).toEqual({});
    expect(imapPayload({ ...IMAP_DEFAULTS, username: "u", password: "p" })).toEqual({});
  });

  it("sends host, port and the matching security mode", () => {
    expect(imapPayload({ ...IMAP_DEFAULTS, host: " imap.x.test " })).toEqual({
      imap_host: "imap.x.test",
      imap_port: 993,
      imap_security_mode: "IMPLICIT_TLS",
    });
    expect(imapPayload({ host: "imap.x.test", port: 143, username: "u", password: "p" })).toEqual({
      imap_host: "imap.x.test",
      imap_port: 143,
      imap_security_mode: "STARTTLS",
      imap_username: "u",
      imap_password: "p",
    });
  });

  it("maps ports to security modes", () => {
    expect(imapSecurityModeForPort(993)).toBe("IMPLICIT_TLS");
    expect(imapSecurityModeForPort(143)).toBe("STARTTLS");
  });
});

describe("ImapSettingsFields", () => {
  it("explains what IMAP is for and edits the settings", async () => {
    const user = userEvent.setup();
    let latest = IMAP_DEFAULTS;
    render(<Harness onValue={(v) => (latest = v)} />);

    expect(screen.getByText(/cannot see replies/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/incoming mail server/i), "imap.x.test");
    await user.selectOptions(screen.getByLabelText(/imap encryption/i), "143");
    expect(latest.host).toBe("imap.x.test");
    expect(latest.port).toBe(143);
  });
});
