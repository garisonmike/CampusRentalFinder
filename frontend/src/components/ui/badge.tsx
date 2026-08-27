import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * A small label.
 *
 * **No colour-only variants.** A badge that says what it means with a hue says
 * nothing to a reader with deuteranopia, nothing in the tenant palette that
 * happens to be grey, and nothing in a screenshot printed in black and white.
 * Every variant here changes weight, border or fill *as well as* colour, and
 * the text always says the thing on its own.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium leading-tight",
  {
    variants: {
      variant: {
        /** A fact about the listing: amenities, unit type, county. */
        neutral: "border-border bg-muted text-muted-foreground",
        /** Something the reader should weigh, not something wrong. */
        note: "border-foreground/25 bg-background text-foreground font-semibold",
        /** The tenant's own colour, for the one thing per card that leads. */
        brand: "border-transparent bg-accent text-accent-foreground",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
