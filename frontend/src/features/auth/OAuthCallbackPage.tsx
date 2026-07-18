import { LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/useAuth";
import { Button, InlineAlert, Panel } from "../../components/ui";

export function OAuthCallbackPage(): React.JSX.Element {
  const { refreshSession } = useAuth();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    void refreshSession().then((authenticated) => {
      if (!active) return;
      if (authenticated) {
        navigate("/repositories", { replace: true });
      } else {
        setFailed(true);
      }
    });
    return () => {
      active = false;
    };
  }, [navigate, refreshSession]);

  return (
    <main className="sign-in">
      <Panel className="sign-in__panel callback-panel">
        {failed ? (
          <>
            <p className="eyebrow">Authorization was not completed</p>
            <h1>We could not finish your GitHub session.</h1>
            <InlineAlert tone="warning">
              Your GitHub authorization may have been declined or expired. No access token was saved
              in this browser.
            </InlineAlert>
            <div className="button-row">
              <Link className="button button--primary" to="/signin">
                Return to sign in
              </Link>
              <Button onClick={() => window.location.reload()}>Retry session check</Button>
            </div>
          </>
        ) : (
          <>
            <LoaderCircle aria-hidden="true" className="spinner" size={22} />
            <p className="eyebrow">GitHub authorization</p>
            <h1>Finishing your secure session.</h1>
            <p>RepoLume is confirming the browser session without exposing GitHub credentials.</p>
          </>
        )}
      </Panel>
    </main>
  );
}
