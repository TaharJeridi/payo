import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/", label: "Panoramica", end: true },
  { to: "/payments", label: "Pagamenti", end: false },
  { to: "/checkout", label: "Nuovo checkout", end: false },
  { to: "/assistant", label: "Assistente", end: false },
  { to: "/status", label: "Stato sistema", end: false },
];

export function Layout() {
  const { logout } = useAuth();
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-2 px-4 py-3">
          <span className="text-lg font-bold text-indigo-700">PayFlow AI</span>
          <nav aria-label="Principale" className="flex flex-1 flex-wrap gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium ${
                    isActive ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">Stripe TEST</span>
          <button type="button" onClick={logout} className="text-sm font-medium text-slate-600 hover:text-slate-900">
            Esci
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
