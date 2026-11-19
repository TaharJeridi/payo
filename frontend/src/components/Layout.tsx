import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Logo } from "./Logo";

const ICONS: Record<string, ReactNode> = {
  overview: <path d="M4 13h6V4H4v9Zm0 7h6v-4H4v4Zm10 0h6v-9h-6v9Zm0-16v4h6V4h-6Z" />,
  payments: <path d="M4 6h16M4 12h16M4 18h10" />,
  checkout: <path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Zm0 3h18M12 13v4m-2-2h4" />,
  assistant: <path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-7l-4 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm4 6h.01M12 11h.01M15 11h.01" />,
  status: <path d="M3 12h4l3-7 4 14 3-7h4" />,
};

const NAV = [
  { to: "/", label: "Overview", icon: "overview", end: true },
  { to: "/payments", label: "Payments", icon: "payments", end: false },
  { to: "/checkout", label: "New checkout", icon: "checkout", end: false },
  { to: "/assistant", label: "Ask Payo", icon: "assistant", end: false },
  { to: "/status", label: "System status", icon: "status", end: false },
];

function NavIcon({ name }: { name: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-5 w-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICONS[name]}
    </svg>
  );
}

/*
 * One navigation for every screen size: a navy sidebar from the lg breakpoint (1024 px),
 * a navy top bar with a horizontally scrollable link row below it.
 */
export function Layout() {
  const { logout } = useAuth();
  return (
    <div className="min-h-screen lg:pl-64">
      <aside className="relative bg-navy text-white lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-64 lg:flex-col">
        <div className="px-4 py-3 lg:px-6 lg:py-6">
          <Logo tone="dark" />
        </div>
        <nav
          aria-label="Main"
          className="flex gap-1 overflow-x-auto px-2 pb-2 lg:flex-1 lg:flex-col lg:overflow-visible lg:px-3 lg:pb-0"
        >
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-accent ${
                  isActive ? "bg-white/10 text-white" : "text-blue-100 hover:bg-white/5 hover:text-white"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span className={isActive ? "text-accent" : ""}>
                    <NavIcon name={item.icon} />
                  </span>
                  {item.label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="absolute right-4 top-4 flex items-center gap-3 lg:static lg:justify-between lg:border-t lg:border-white/10 lg:px-6 lg:py-4">
          <TestModeBadge />
          <LogoutButton onClick={logout} />
        </div>
      </aside>
      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-10 lg:py-10">
        <Outlet />
      </main>
    </div>
  );
}

function TestModeBadge() {
  return (
    <span className="whitespace-nowrap rounded-md bg-accent px-2 py-0.5 text-xs font-semibold text-ink">Stripe TEST</span>
  );
}

function LogoutButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-sm font-medium text-blue-100 hover:text-white focus-visible:outline-accent"
    >
      Sign out
    </button>
  );
}
