export type DatePreset = "today" | "yesterday" | "last_7_days" | "last_30_days";

export const DATE_PRESETS: { value: DatePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "last_7_days", label: "7 Days" },
  { value: "last_30_days", label: "30 Days" },
];

export function getDateRangeForPreset(preset: DatePreset): { startDate: string; endDate: string } {
  const now = new Date();
  const todayStr = now.toISOString().slice(0, 10);

  if (preset === "today") {
    return { startDate: todayStr, endDate: todayStr };
  }
  if (preset === "yesterday") {
    const yest = new Date(now);
    yest.setDate(now.getDate() - 1);
    const yestStr = yest.toISOString().slice(0, 10);
    return { startDate: yestStr, endDate: yestStr };
  }
  if (preset === "last_7_days") {
    const d7 = new Date(now);
    d7.setDate(now.getDate() - 6);
    return { startDate: d7.toISOString().slice(0, 10), endDate: todayStr };
  }
  // last_30_days
  const d30 = new Date(now);
  d30.setDate(now.getDate() - 29);
  return { startDate: d30.toISOString().slice(0, 10), endDate: todayStr };
}
