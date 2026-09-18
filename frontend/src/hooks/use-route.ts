import { useCallback, useSyncExternalStore } from "react";

/** The console's workspaces, addressable by URL hash so reloads and links keep the view. */
export type View = "review" | "approved" | "vendors" | "team";

const HASH: Record<View, string> = { review: "#/review", approved: "#/approved", vendors: "#/vendors", team: "#/team" };

function parse(hash: string): View {
  if (hash.startsWith("#/approved")) return "approved";
  if (hash.startsWith("#/vendors")) return "vendors";
  if (hash.startsWith("#/team")) return "team";
  return "review";
}

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

const snapshot = () => parse(window.location.hash);

export function navigate(view: View) {
  if (parse(window.location.hash) === view && window.location.hash) return;
  window.location.hash = HASH[view];
  window.scrollTo({ top: 0 });
}

export function useRoute(): { view: View; navigate: (view: View) => void; href: (view: View) => string } {
  const view = useSyncExternalStore(subscribe, snapshot, () => "review" as View);
  const go = useCallback((next: View) => navigate(next), []);
  return { view, navigate: go, href: (next) => HASH[next] };
}
