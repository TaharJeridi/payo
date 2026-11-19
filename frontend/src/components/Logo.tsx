/** Payo mark: a rounded yellow tile with a stylised navy "p". Same drawing as public/favicon.svg. */
export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" className="fill-accent" />
      <path
        d="M11 25V12.5a5.5 5.5 0 1 1 5.5 5.5H11"
        fill="none"
        className="stroke-navy"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Wordmark "payo." with the yellow full stop; `tone` picks the text colour for light or navy backgrounds. */
export function Logo({ tone = "light", className = "" }: { tone?: "light" | "dark"; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`} aria-label="Payo" role="img">
      <LogoMark />
      <span
        aria-hidden="true"
        className={`text-2xl font-extrabold lowercase tracking-tight ${tone === "dark" ? "text-white" : "text-navy"}`}
      >
        payo<span className="text-accent">.</span>
      </span>
    </span>
  );
}
