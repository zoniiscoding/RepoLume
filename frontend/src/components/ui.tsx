import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  PropsWithChildren,
  TextareaHTMLAttributes,
} from "react";
import { forwardRef } from "react";
import { LoaderCircle } from "lucide-react";

type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { children, className = "", variant = "secondary", loading = false, disabled, ...props },
  ref,
): React.JSX.Element {
  return (
    <button
      ref={ref}
      className={`button button--${variant} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <LoaderCircle aria-hidden="true" className="spinner" size={16} /> : null}
      <span>{children}</span>
    </button>
  );
});

export function Input({
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement>): React.JSX.Element {
  return <input className={`input ${className}`} {...props} />;
}

export function Textarea({
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>): React.JSX.Element {
  return <textarea className={`textarea ${className}`} {...props} />;
}

export function Panel({
  children,
  className = "",
}: PropsWithChildren<{ className?: string }>): React.JSX.Element {
  return <section className={`panel ${className}`}>{children}</section>;
}

export function InlineAlert({
  children,
  tone = "neutral",
}: PropsWithChildren<{ tone?: "neutral" | "warning" | "error" | "success" }>): React.JSX.Element {
  return (
    <div
      className={`inline-alert inline-alert--${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: PropsWithChildren<{ title: string; action?: React.ReactNode }>): React.JSX.Element {
  return (
    <section className="empty-state">
      <h2>{title}</h2>
      <div className="empty-state__body">{children}</div>
      {action ? <div className="empty-state__action">{action}</div> : null}
    </section>
  );
}

export function Skeleton({ className = "" }: { className?: string }): React.JSX.Element {
  return <span aria-hidden="true" className={`skeleton ${className}`} />;
}
