import { Skeleton } from "./ui";

export function LoadingPage(): React.JSX.Element {
  return (
    <main className="loading-page" aria-label="Loading application">
      <Skeleton className="loading-page__bar" />
      <Skeleton className="loading-page__line" />
      <Skeleton className="loading-page__line loading-page__line--short" />
    </main>
  );
}
