import * as React from "react";

import { cn } from "@/lib/utils";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: false;
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "default" | "sm" | "icon";
};

type ButtonAsChildProps = {
  asChild: true;
  children: React.ReactElement<{ className?: string }>;
  className?: string;
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "default" | "sm" | "icon";
};

type Props = ButtonProps | ButtonAsChildProps;

const variants = {
  primary: "bg-slate-950 text-white hover:bg-slate-800",
  secondary: "bg-slate-100 text-slate-900 hover:bg-slate-200",
  outline: "border border-slate-200 bg-white hover:bg-slate-100 text-slate-900",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-950",
  danger: "bg-red-600 text-white hover:bg-red-700",
};

const sizes = {
  default: "h-10 px-4 py-2",
  sm: "h-8 rounded-md px-3 text-xs",
  icon: "h-9 w-9 p-0",
};

export function Button(props: Props) {
  const { variant = "primary", size = "default", className } = props;
  const classes = cn(
    "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:pointer-events-none disabled:opacity-50",
    variants[variant],
    sizes[size],
    className,
  );

  if (props.asChild) {
    const child = props.children;
    return React.cloneElement(child, {
      className: cn(classes, child.props.className),
    });
  }

  const buttonProps = { ...props };
  delete buttonProps.asChild;
  delete buttonProps.variant;
  delete buttonProps.size;
  delete buttonProps.className;
  return <button className={classes} {...buttonProps} />;
}
