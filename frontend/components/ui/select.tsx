import * as React from "react";

import { controlClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";

// Styled native <select>: accessible and keyboard-friendly without a
// dependency, matching Input's look.
export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <select ref={ref} className={cn(controlClasses, "h-10 px-3", className)} {...props}>
    {children}
  </select>
));
Select.displayName = "Select";
