import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { api, navigate, useLocation } from "./lib";
import { Today } from "./pages/Today";
import { RaceCards } from "./pages/RaceCards";
import { Race } from "./pages/Race";
import { Opportunities } from "./pages/Opportunities";
import { Performance } from "./pages/Performance";
import { History } from "./pages/History";
import { Documents } from "./pages/Documents";

const NAV = [
  ["/", "Today"],
  ["/cards", "Race Cards"],
  ["/opportunities", "Best Opportunities"],
  ["/performance", "Performance"],
  ["/history", "History"],
  ["/documents", "Documents"],
] as const;

export function Link({ to, children, className }: { to: string; children: React.ReactNode; className?: string }) {
  return (
    <a
      href={to}
      className={className}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

function Logo({ size = 40 }: { size?: number }) {
  return <img src="/logo.svg" width={size} height={size} alt="" aria-hidden="true" />;
}

function Login({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <main className="login">
      <form
        className="login-card"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            await api("/api/login", { method: "POST", body: JSON.stringify({ username: user, password: pw }) });
            onDone();
          } catch (x) {
            setErr((x as Error).message || "Sign-in failed");
          } finally {
            setBusy(false);
          }
        }}
      >
        <Logo size={72} />
        <h1>Rein Man</h1>
        <p className="tagline">The Intelligence Behind the Odds.</p>
        <label htmlFor="user">Username</label>
        <input id="user" autoComplete="username" autoCapitalize="none" value={user} onChange={(e) => setUser(e.target.value)} autoFocus />
        <label htmlFor="pw">Password</label>
        <input id="pw" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <p className="error" role="alert">{err}</p>}
        <button className="btn primary" disabled={busy || !pw || !user}>{busy ? "Checking…" : "Enter"}</button>
        <p className="muted small">Private handicapping system. Authorized use only.</p>
      </form>
    </main>
  );
}

function Shell({ authRequired }: { authRequired: boolean }) {
  const loc = useLocation();
  const path = loc.split("?")[0];
  const [open, setOpen] = useState(false);
  let page: React.ReactNode;
  const raceMatch = path.match(/^\/race\/(.+)$/);
  if (raceMatch) page = <Race id={decodeURIComponent(raceMatch[1])} />;
  else if (path.startsWith("/cards")) page = <RaceCards />;
  else if (path.startsWith("/opportunities")) page = <Opportunities />;
  else if (path.startsWith("/performance")) page = <Performance />;
  else if (path.startsWith("/history")) page = <History />;
  else if (path.startsWith("/documents")) page = <Documents />;
  else page = <Today />;
  const active = (to: string) => (to === "/" ? path === "/" : path.startsWith(to) || (to === "/cards" && !!raceMatch));
  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          <Logo size={38} />
          <span>
            <strong>Rein Man</strong>
            <em>The Intelligence Behind the Odds.</em>
          </span>
        </Link>
        <button className="menu-btn" aria-label="Menu" aria-expanded={open} onClick={() => setOpen(!open)}>
          ☰
        </button>
        <nav className={open ? "open" : ""} onClick={() => setOpen(false)}>
          {NAV.map(([to, label]) => (
            <Link key={to} to={to} className={active(to) ? "active" : ""}>
              {label}
            </Link>
          ))}
          {authRequired && <button
            className="logout"
            onClick={async () => {
              await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {});
              window.dispatchEvent(new Event("reinman:logout"));
            }}
          >
            Sign out
          </button>}
        </nav>
      </header>
      <main className="page">{page}</main>
      <footer className="foot">
        Rein Man records every selection before post and grades it against official results. Past performance is
        not a promise of future results. Hypothetical wagers are tracked separately from real ones.
      </footer>
    </>
  );
}

function App() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [authRequired, setAuthRequired] = useState(true);
  useEffect(() => {
    api<{ authenticated: boolean; authRequired?: boolean }>("/api/session")
      .then((r) => {
        setAuthRequired(r.authRequired !== false);
        setAuth(r.authenticated);
      })
      .catch(() => setAuth(false));
    const out = () => setAuth(false);
    window.addEventListener("reinman:logout", out);
    return () => window.removeEventListener("reinman:logout", out);
  }, []);
  if (auth === null) return <div className="boot"><Logo size={56} /></div>;
  return auth ? <Shell authRequired={authRequired} /> : <Login onDone={() => setAuth(true)} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
