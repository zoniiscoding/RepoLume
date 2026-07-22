import { Github, ShieldCheck } from "lucide-react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../auth/useAuth";
import { Button, InlineAlert, Panel } from "../../components/ui";

export function SignInPage(): React.JSX.Element {
  const { state, signIn } = useAuth();
  const location = useLocation();
  const destination = (location.state as { from?: string } | null)?.from ?? "/repositories";
  if (state === "authenticated") {
    return <Navigate replace to={destination} />;
  }
  return (
    <main className="sign-in">
      <a className="skip-link" href="#sign-in-panel">
        Skip to sign in
      </a>
      <Panel className="sign-in__panel">
        <div className="brand brand--large" aria-label="RepoLume">
          <span className="brand__mark" aria-hidden="true">
            R
          </span>
          <span>RepoLume</span>
        </div>
        <div id="sign-in-panel" className="sign-in__copy">
          <p className="eyebrow">Repository intelligence</p>
          <h1>Understand the code you are authorized to see.</h1>
          <p>
            Sign in with Google or GitHub. Import public repositories by URL, or connect the GitHub
            App for private repositories.
          </p>
        </div>
        {state === "expired" ? (
          <InlineAlert tone="warning">
            Your session ended. Continue with Google or GitHub to start a new session.
          </InlineAlert>
        ) : null}
        <Button className="sign-in__action" variant="primary" onClick={() => signIn("google")}>
          <span aria-hidden="true" className="provider-mark">
            G
          </span>
          Continue with Google
        </Button>
        <Button className="sign-in__action" onClick={() => signIn("github")}>
          <Github aria-hidden="true" size={18} />
          Continue with GitHub
        </Button>
        <div className="sign-in__trust">
          <ShieldCheck aria-hidden="true" size={17} />
          <span>
            Provider tokens stay server-side. Private access still requires repositories approved
            through your GitHub App installation.
          </span>
        </div>
      </Panel>
    </main>
  );
}
