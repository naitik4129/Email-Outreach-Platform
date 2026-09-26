"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

type TriggerState = {
  open: boolean;
  toggle: () => void;
  ariaProps: { "aria-haspopup": "dialog"; "aria-expanded": boolean; "aria-controls": string };
};

type PopoverProps = {
  trigger: (state: TriggerState) => React.ReactNode;
  children: React.ReactNode | ((close: () => void) => React.ReactNode);
  align?: "start" | "end";
  className?: string;
  label: string;
};

// Small anchored panel: closes on outside click and Escape. Not a portal, so it
// stays inside the surrounding dialog's focus trap.
export function Popover({ trigger, children, align = "start", className, label }: PopoverProps) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const panelId = React.useId();

  React.useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  const close = React.useCallback(() => setOpen(false), []);

  return (
    <div ref={rootRef} className="relative inline-block">
      {trigger({
        open,
        toggle: () => setOpen((value) => !value),
        ariaProps: {
          "aria-haspopup": "dialog",
          "aria-expanded": open,
          "aria-controls": panelId,
        },
      })}
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label={label}
          className={cn(
            "absolute z-30 mt-1 min-w-[14rem] rounded-md border border-slate-200 bg-white p-2 shadow-lg",
            align === "end" ? "right-0" : "left-0",
            className,
          )}
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      ) : null}
    </div>
  );
}
