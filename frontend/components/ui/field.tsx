import * as React from "react";
import { AlertCircle } from "lucide-react";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

type FieldProps = {
  id?: string;
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactElement<{ id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }>;
  className?: string;
};

export function Field({ id: explicitId, label, required, error, children, className }: FieldProps) {
  const generatedId = React.useId();
  const id = explicitId || children.props.id || generatedId;
  const errorId = `${id}-error`;
  const child = React.cloneElement(children, {
    id,
    "aria-invalid": Boolean(error),
    "aria-describedby": error ? errorId : undefined,
  });

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>
        {label}
        {required && <span className="text-red-500 ml-0.5" aria-hidden="true">*</span>}
      </Label>
      {child}
      {error ? (
        <p
          id={errorId}
          role="alert"
          className="flex items-center gap-1.5 text-xs font-medium text-red-600"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      ) : null}
    </div>
  );
}
