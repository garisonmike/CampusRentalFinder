import { Toaster as Sonner, toast } from "sonner";

type ToasterProps = React.ComponentProps<typeof Sonner>;

/**
 * Toast host.
 *
 * The shadcn original read the theme from next-themes, which this project no
 * longer uses. Sonner's own "system" setting follows prefers-color-scheme
 * directly, which is what the stylesheet does too.
 */
export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="system"
      position="top-right"
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton: "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton: "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}

export { toast };
