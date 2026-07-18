import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";
import { LoadingPage } from "../components/LoadingPage";

export function RequireAuth(): React.JSX.Element {
  const { state } = useAuth();
  const location = useLocation();
  if (state === "loading") {
    return <LoadingPage />;
  }
  if (state !== "authenticated") {
    return <Navigate replace to="/signin" state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
