import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

export const buttonVariants = cva(
  [
    "relative inline-flex shrink-0 cursor-pointer select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium",
    "transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out active:scale-[0.98]",
    "disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-3.5 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent shadow-[var(--shadow-card)] hover:bg-accent-hover",
        approve:
          "bg-approved text-on-approved shadow-[var(--shadow-card),inset_0_1px_0_rgb(255_255_255/0.18)] hover:bg-approved-hover focus-visible:outline-approved/70",
        secondary: "border border-line bg-surface-solid text-ink shadow-[var(--shadow-card)] hover:border-line-strong hover:bg-hover",
        outline: "border border-line text-ink-2 hover:border-line-strong hover:bg-hover hover:text-ink",
        ghost: "text-ink-2 hover:bg-hover hover:text-ink",
        danger: "bg-rejected text-on-rejected shadow-[var(--shadow-card)] hover:bg-rejected/90",
        "danger-outline": "border border-rejected/40 text-rejected hover:bg-rejected/10",
        "approve-outline": "border border-approved/40 text-approved hover:bg-approved/10",
      },
      size: {
        xs: "h-6 rounded-[5px] px-2 text-[12px]",
        sm: "h-7 px-2.5 text-[12px]",
        md: "h-8 px-3 text-[13px]",
        lg: "h-9 px-4 text-[13px]",
        icon: "size-8",
        "icon-sm": "size-7",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

type ButtonProps = ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ className, variant, size, asChild = false, type = "button", ...props }: ButtonProps) {
  const Component = asChild ? Slot.Root : "button";
  return <Component type={asChild ? undefined : type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
