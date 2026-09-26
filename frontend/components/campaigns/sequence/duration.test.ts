import { describe, expect, it } from "vitest";

import {
  computeTimings,
  formatDuration,
  sequenceProblems,
  splitDuration,
  toMinutes,
  validateDuration,
  waitMinutesForDay,
} from "@/components/campaigns/sequence/duration";
import type { SequenceStep } from "@/types/domain";

function step(
  id: string,
  position: number,
  kind: "EMAIL" | "WAIT",
  wait: number | null = null,
): SequenceStep {
  return {
    id,
    sequence_id: "s",
    campaign_id: "c",
    position,
    kind,
    email_subject: kind === "EMAIL" ? "Subject" : null,
    email_body_html: kind === "EMAIL" ? "<p>x</p>" : null,
    email_variable_schema: null,
    wait_duration_minutes: wait,
    source_template_version_id: null,
    version: 1,
    created_at: "",
    updated_at: "",
  };
}

describe("duration helpers", () => {
  it("converts units to minutes", () => {
    expect(toMinutes(2, "days")).toBe(2880);
    expect(toMinutes(3, "hours")).toBe(180);
    expect(toMinutes(45, "minutes")).toBe(45);
  });

  it("splits into the largest exact unit without rounding", () => {
    expect(splitDuration(2880)).toEqual({ value: 2, unit: "days" });
    expect(splitDuration(180)).toEqual({ value: 3, unit: "hours" });
    expect(splitDuration(90)).toEqual({ value: 90, unit: "minutes" });
    expect(splitDuration(1500)).toEqual({ value: 25, unit: "hours" });
  });

  it("formats readable compound durations", () => {
    expect(formatDuration(1440)).toBe("1 day");
    expect(formatDuration(2880)).toBe("2 days");
    expect(formatDuration(1500)).toBe("1 day 1 hour");
    expect(formatDuration(61)).toBe("1 hour 1 minute");
    expect(formatDuration(5)).toBe("5 minutes");
  });

  it("validates whole numbers within the server limit", () => {
    expect(validateDuration(2, "days")).toBeNull();
    expect(validateDuration(365, "days")).toBeNull();
    expect(validateDuration(366, "days")).toMatch(/at most/i);
    expect(validateDuration(0, "hours")).toMatch(/at least/i);
    expect(validateDuration(1.5, "days")).toMatch(/whole number/i);
    expect(validateDuration(Number.NaN, "days")).toMatch(/whole number/i);
  });

  it("maps a target day to a whole-day wait", () => {
    expect(waitMinutesForDay(1, 3)).toBe(2880);
    expect(waitMinutesForDay(3, 4)).toBe(1440);
    expect(waitMinutesForDay(3, 3)).toBeNull();
    expect(waitMinutesForDay(3, 2)).toBeNull();
    expect(waitMinutesForDay(1, 400)).toBeNull();
  });
});

describe("computeTimings", () => {
  it("accumulates waits into Day numbers", () => {
    const steps = [
      step("e1", 1, "EMAIL"),
      step("w1", 2, "WAIT", 2880),
      step("e2", 3, "EMAIL"),
      step("w2", 4, "WAIT", 4320),
      step("e3", 5, "EMAIL"),
    ];
    const timings = computeTimings(steps);
    expect(timings.get("e1")).toEqual({ offsetMinutes: 0, day: 1 });
    expect(timings.get("e2")).toEqual({ offsetMinutes: 2880, day: 3 });
    expect(timings.get("e3")).toEqual({ offsetMinutes: 7200, day: 6 });
  });

  it("counts partial days down (hours-based waits)", () => {
    const timings = computeTimings([
      step("e1", 1, "EMAIL"),
      step("w1", 2, "WAIT", 180),
      step("e2", 3, "EMAIL"),
    ]);
    expect(timings.get("e2")).toEqual({ offsetMinutes: 180, day: 1 });
  });
});

describe("sequenceProblems", () => {
  it("is empty for an alternating sequence and for no steps", () => {
    expect(sequenceProblems([])).toEqual([]);
    expect(
      sequenceProblems([step("e1", 1, "EMAIL"), step("w", 2, "WAIT", 60), step("e2", 3, "EMAIL")]),
    ).toEqual([]);
  });
  it("flags start/end/alternation problems", () => {
    expect(sequenceProblems([step("w", 1, "WAIT", 60), step("e", 2, "EMAIL")])).toContain(
      "The sequence must start with an email.",
    );
    expect(sequenceProblems([step("e", 1, "EMAIL"), step("w", 2, "WAIT", 60)])).toContain(
      "The sequence must end with an email.",
    );
    expect(sequenceProblems([step("e1", 1, "EMAIL"), step("e2", 2, "EMAIL")])).toContain(
      "Emails and waits must alternate.",
    );
  });
});
