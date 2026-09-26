"use client";

import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { SmtpSecurityMode } from "@/types/domain";

export type ImapSettings = {
  host: string;
  port: number;
  username: string;
  password: string;
};

export const IMAP_DEFAULTS: ImapSettings = {
  host: "",
  port: 993,
  username: "",
  password: "",
};

/** IMAP uses implicit TLS on 993 and STARTTLS on 143. */
export function imapSecurityModeForPort(port: number): SmtpSecurityMode {
  return port === 143 ? "STARTTLS" : "IMPLICIT_TLS";
}

/**
 * SMTP can only send. To detect replies (and stop a sequence when a lead
 * replies) Outly reads the same mailbox over IMAP, so those settings are asked
 * for alongside the SMTP ones. The IMAP login defaults to the SMTP login.
 */
export function ImapSettingsFields({
  idPrefix,
  value,
  onChange,
  disabled,
  passwordLabel = "Incoming mail app secret",
}: {
  idPrefix: string;
  value: ImapSettings;
  onChange: (next: ImapSettings) => void;
  disabled?: boolean;
  passwordLabel?: string;
}) {
  const set = (patch: Partial<ImapSettings>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-4 rounded-lg border border-slate-200 bg-slate-50/60 p-4">
      <div>
        <p className="text-sm font-semibold text-slate-900">Reply detection (IMAP)</p>
        <p className="mt-0.5 text-xs text-slate-500">
          Optional. Without IMAP settings this mailbox can send but Outly cannot see replies,
          so follow-ups will not stop when a lead answers. The IMAP login defaults to the SMTP
          username and password.
        </p>
      </div>
      <Field id={`${idPrefix}-imap-host`} label="Incoming mail server (IMAP)">
        <Input
          value={value.host}
          onChange={(e) => set({ host: e.target.value })}
          placeholder="imap.example.com"
          disabled={disabled}
          autoComplete="off"
        />
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-imap-port`}>IMAP encryption</Label>
          <select
            id={`${idPrefix}-imap-port`}
            value={value.port === 143 ? 143 : 993}
            onChange={(e) => set({ port: Number(e.target.value) })}
            disabled={disabled}
            className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value={993}>Implicit TLS (993)</option>
            <option value={143}>STARTTLS (143)</option>
          </select>
        </div>
        <Field id={`${idPrefix}-imap-username`} label="Incoming mail login (optional)">
          <Input
            value={value.username}
            onChange={(e) => set({ username: e.target.value })}
            disabled={disabled}
            autoComplete="off"
          />
        </Field>
      </div>
      <Field id={`${idPrefix}-imap-password`} label={`${passwordLabel} (optional)`}>
        <Input
          type="password"
          value={value.password}
          onChange={(e) => set({ password: e.target.value })}
          disabled={disabled}
          autoComplete="new-password"
        />
      </Field>
    </div>
  );
}

/** Payload fragment for the API: nothing at all when no host was entered. */
export function imapPayload(value: ImapSettings) {
  const host = value.host.trim();
  if (!host) return {};
  return {
    imap_host: host,
    imap_port: value.port,
    imap_security_mode: imapSecurityModeForPort(value.port),
    ...(value.username.trim() ? { imap_username: value.username.trim() } : {}),
    ...(value.password ? { imap_password: value.password } : {}),
  };
}
