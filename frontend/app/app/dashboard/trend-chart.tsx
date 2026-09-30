"use client";

import { useState } from "react";

import type { TimeSeriesBucket } from "@/types/analytics";

export function TrendChart({ trend }: { trend: TimeSeriesBucket[] }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  if (!trend || trend.length === 0) {
    return (
      <div className="grid h-48 place-items-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-500">
        No trend data available for this date window.
      </div>
    );
  }

  const maxSent = Math.max(...trend.map((t) => t.sent), 1);
  const chartHeight = 160;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-6 text-xs">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-sm bg-brand-600" />
            <span className="font-medium text-slate-700">Emails Sent</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-sm bg-emerald-500" />
            <span className="font-medium text-slate-700">Replies</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-sm bg-red-500" />
            <span className="font-medium text-slate-700">Bounces</span>
          </div>
        </div>
        {hoveredIndex !== null && trend[hoveredIndex] && (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700 shadow-sm">
            <span className="font-semibold text-slate-900">{trend[hoveredIndex].date}:</span>{" "}
            {trend[hoveredIndex].sent} sent &bull; {trend[hoveredIndex].replies} replies &bull;{" "}
            {trend[hoveredIndex].bounces} bounces
          </div>
        )}
      </div>

      <div className="relative pt-6">
        <div className="flex h-44 items-end gap-1.5 sm:gap-2">
          {trend.map((bucket, idx) => {
            const sentHeight = Math.max(Math.round((bucket.sent / maxSent) * chartHeight), 4);
            const isHovered = hoveredIndex === idx;

            return (
              <div
                key={bucket.date}
                className="group relative flex flex-1 flex-col items-center"
                onMouseEnter={() => setHoveredIndex(idx)}
                onMouseLeave={() => setHoveredIndex(null)}
              >
                {/* Visual tooltip */}
                <div
                  className={`pointer-events-none absolute -top-12 z-10 hidden whitespace-nowrap rounded bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-white shadow transition-all group-hover:block`}
                >
                  <p className="font-semibold">{bucket.date}</p>
                  <p>
                    Sent: {bucket.sent} | Rep: {bucket.replies} | Bnc: {bucket.bounces}
                  </p>
                </div>

                {/* Stacked bar visualization */}
                <div className="flex w-full max-w-[28px] flex-col items-center justify-end">
                  <div
                    style={{ height: `${sentHeight}px` }}
                    className={`w-full rounded-t transition-all ${
                      isHovered ? "bg-brand-700 ring-2 ring-brand-400" : "bg-brand-500/90"
                    }`}
                  />
                </div>

                {/* X axis date label */}
                <span className="mt-2 block truncate text-[10px] text-slate-400">
                  {trend.length <= 10
                    ? bucket.date.slice(5)
                    : idx % Math.ceil(trend.length / 7) === 0
                      ? bucket.date.slice(5)
                      : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
