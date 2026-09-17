import { KeyRound, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

/** Why a sign-in attempt came back without a session. Keyed by `?auth_error=` from the callback. */
const REASONS: Record<string, string> = {
  signup_closed: "New workspaces are limited to approved Google accounts on this deployment. Ask someone to invite you to their workspace.",
  workspace_limit: "That Google account already owns the maximum number of workspaces.",
  cancelled: "Sign-in was cancelled.",
  email_unverified: "That Google account's email address isn't verified.",
  state_expired: "The sign-in attempt took too long. Try again.",
  state_mismatch: "The sign-in attempt couldn't be verified. Try again.",
  exchange_failed: "Google rejected the sign-in attempt. Try again.",
  google_unreachable: "Google couldn't be reached. Try again in a moment.",
  invalid_id_token: "Google's response couldn't be verified. Try again.",
  no_email: "Google didn't share an email address for that account.",
  no_code: "Google didn't complete the sign-in. Try again.",
  not_configured: "Google sign-in isn't configured on this deployment yet.",
};

/** Reads and clears `?auth_error=` so a refresh doesn't show a stale message. */
function useAuthError(): string | null {
  const [reason, setReason] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const value = params.get("auth_error");
    if (!value) return;
    setReason(value);
    params.delete("auth_error");
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
  }, []);
  return reason;
}

/** The gate in front of the console when nobody is signed in. */
export function SignIn({ googleLogin }: { googleLogin: boolean }) {
  const reason = useAuthError();
  const message = reason ? (REASONS[reason] ?? "Sign-in didn't complete. Try again.") : null;

  return (
    <main className="mx-auto flex min-h-[70dvh] max-w-[1600px] items-center justify-center px-4 py-16 sm:px-6">
      <section className="glass w-full max-w-md animate-rise-in rounded-2xl px-6 py-7 text-center">
        <span
          className="mx-auto flex size-12 items-center justify-center rounded-2xl bg-accent text-on-accent shadow-[var(--shadow-card),inset_0_1px_0_rgb(255_255_255/0.2)]"
          aria-hidden
        >
          <ShieldCheck className="size-6" strokeWidth={1.75} />
        </span>
        <h1 className="mt-4 text-[18px] font-semibold tracking-[-0.02em] text-ink">Invoice Auditor</h1>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-2">
          Sign in with Google to open your workspace. Your invoices stay private to your workspace unless you invite people to it.
        </p>

        {message ? (
          <p role="alert" className="mt-4 rounded-lg border border-review/30 bg-review/[0.08] px-3 py-2.5 text-left text-[12.5px] leading-relaxed text-ink-2">
            {message}
          </p>
        ) : null}

        {googleLogin ? (
          <>
            <Button variant="secondary" size="lg" className="mt-5 w-full justify-center gap-2.5" asChild>
              <a href={api.signInUrl()}>
                <GoogleMark />
                Continue with Google
              </a>
            </Button>
            <p className="mt-4 text-[11.5px] leading-relaxed text-ink-3">
              New here? Signing in creates your own private workspace. If someone invited you, you'll find their workspace in
              the menu at the top left.
            </p>
          </>
        ) : (
          <div className="mt-5 rounded-lg border border-line bg-sunken/60 px-3.5 py-3 text-left">
            <p className="flex items-center gap-2 text-[13px] font-medium text-ink">
              <KeyRound className="size-4 text-ink-3" aria-hidden />
              Google sign-in isn't configured
            </p>
            <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">
              Set <span className="figure text-ink">GOOGLE_CLIENT_ID</span>, <span className="figure text-ink">GOOGLE_CLIENT_SECRET</span> and{" "}
              <span className="figure text-ink">JWT_SECRET</span> in the deployment's environment variables, then redeploy. Locally, running
              the console through the Vite dev server authenticates with the API key in{" "}
              <span className="figure text-ink">frontend/.env.local</span> instead.
            </p>
          </div>
        )}
      </section>
    </main>
  );
}

/** Google's mark, inline so no third-party asset is loaded. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" className="!size-4" aria-hidden>
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z" />
      <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z" />
    </svg>
  );
}
