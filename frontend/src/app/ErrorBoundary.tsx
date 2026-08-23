import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches render-time errors so one broken subtree does not blank the page.
 *
 * A class component because React has no hook equivalent for
 * componentDidCatch.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Replace with the error reporter when one exists.
    console.error("Unhandled render error", error, info.componentStack);
  }

  handleReset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    if (this.props.fallback) return this.props.fallback;

    return (
      <div role="alert" className="mx-auto flex max-w-md flex-col gap-4 p-8 text-center">
        <h1 className="text-2xl font-semibold">Something went wrong</h1>
        <p className="text-muted-foreground">
          The page could not be displayed. Trying again may help.
        </p>
        <div>
          <Button onClick={this.handleReset}>Try again</Button>
        </div>
      </div>
    );
  }
}
