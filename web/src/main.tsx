import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { api, navigate, useLocation } from "./lib";
import { Today } from "./pages/Today";
import { Loading } from "./pages/common";

// Today loads with the app; every other page is fetched when first opened
// (Vercel rules bundle-dynamic-imports / bundle-conditional), so phones download less up front.
const RaceCards = lazy(() => import("./pages/RaceCards").then((m) => ({ default: m.RaceCards })));
const Race = lazy(() => import("./pages/Race").then((m) => ({ default: m.Race })));
const Opportunities = lazy(() => import("./pages/Opportunities").then((m) => ({ default: m.Opportunities })));
const Performance = lazy(() => import("./pages/Performance").then((m) => ({ default: m.Performance })));
const History = lazy(() => import("./pages/History").then((m) => ({ default: m.History })));
const Documents = lazy(() => import("./pages/Documents").then((m) => ({ default: m.Documents })));

const Stables = lazy(() => import("./pages/Stables").then(m => ({ default: m.Stables })));

const NAV = [
  ["/", "Today"],
  ["/cards", "Race Cards"],
  ["/opportunities", "Value"],
  ["/performance", "Performance"],
  ["/history", "History"],
  ["/documents", "Documents"],
  ["/stables", "The Stables"],
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
  return <img src="/gentleman-192.png" className="gentleman-logo" width={size} height={size} alt="" aria-hidden="true" />;
}

function AuthCard({ children, onSubmit }: { children: React.ReactNode; onSubmit: (e: React.FormEvent) => void }) {
  return (
    <main className="login">
      <form className="login-card" onSubmit={onSubmit}>
        <Logo size={100} />
        <h1>Rein Man</h1>
        <p className="tagline">The Intelligence Behind the Odds.</p>
        {children}
      </form>
    </main>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "forgot">("login");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      if (mode === "login") {
        await api("/api/login", { method: "POST", body: JSON.stringify({ email, password: pw }) });
        onDone();
      } else {
        const r = await api<{ message: string }>("/api/forgot", { method: "POST", body: JSON.stringify({ email }) });
        setMsg(r.message);
      }
    } catch (x) {
      setErr((x as Error).message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthCard onSubmit={submit}>
      <label htmlFor="email">Email</label>
      <input id="email" type="email" autoComplete="username" autoCapitalize="none" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
      {mode === "login" && (
        <>
          <label htmlFor="pw">Password</label>
          <input id="pw" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
        </>
      )}
      {err && <p className="error" role="alert">{err}</p>}
      {msg && <p className="ok" role="status">{msg}</p>}
      <button className="btn primary" disabled={busy || !email || (mode === "login" && !pw)}>
        {busy ? "One moment…" : mode === "login" ? "Sign in" : "Email me a reset link"}
      </button>
      <button type="button" className="linkish small" onClick={() => { setMode(mode === "login" ? "forgot" : "login"); setErr(null); setMsg(null); }}>
        {mode === "login" ? "Forgot password?" : "Back to sign in"}
      </button>
      <p className="muted small">Private racing intelligence.</p>
    </AuthCard>
  );
}

function Reset() {
  const token = new URLSearchParams(location.search).get("token") ?? "";
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (pw !== pw2) return setErr("The two passwords don't match.");
    setBusy(true);
    try {
      await api("/api/reset", { method: "POST", body: JSON.stringify({ token, password: pw }) });
      location.replace("/");
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthCard onSubmit={submit}>
      <p className="small">Choose a new password (at least 10 characters). Every other signed-in browser will be signed out.</p>
      <label htmlFor="pw">New password</label>
      <input id="pw" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
      <label htmlFor="pw2">Repeat new password</label>
      <input id="pw2" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
      {!token && <p className="error">This link is missing its reset code. Use the link from the email.</p>}
      {err && <p className="error" role="alert">{err}</p>}
      <button className="btn primary" disabled={busy || !token || pw.length < 10 || !pw2}>{busy ? "Saving…" : "Set new password"}</button>
    </AuthCard>
  );
}

function Shell({ authRequired }: { authRequired: boolean }) {
  const loc = useLocation();
  const path = loc.split("?")[0];
  const [open, setOpen] = useState(false);
  let page: React.ReactNode;
  const raceMatch = path.match(/^\/race\/(.+)$/);
  if (raceMatch) page = <Race id={decodeURIComponent(raceMatch[1])} />;
  else if (path === "/stables" || path.startsWith("/stables/")) page = <Stables id={path.startsWith("/stables/") ? decodeURIComponent(path.slice(9)) : undefined} />;
  else if (path.startsWith("/cards")) page = <RaceCards />;
  else if (path.startsWith("/opportunities")) page = <Opportunities />;
  else if (path.startsWith("/performance")) page = <Performance />;
  else if (path.startsWith("/history")) page = <History />;
  else if (path.startsWith("/documents")) page = <Documents />;
  else page = <Today />;
  const active = (to: string) => (to === "/" ? path === "/" : path.startsWith(to) || (to === "/cards" && !!raceMatch));
  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="topbar">
        <Link to="/" className="brand">
          <Logo size={52} />
          <span>
            <strong>Rein Man</strong>
            <em>The Intelligence Behind the Odds.</em>
          </span>
        </Link>
        <button className="menu-btn" aria-label="Menu" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span aria-hidden="true">☰</span>
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
      <main className="page" id="main" tabIndex={-1}><Suspense fallback={<Loading />}>{page}</Suspense></main>
      <footer className="foot">
        <div className="footer-brand"><Logo size={34} /><strong>Rein Man</strong></div>
        Selections are recorded before post time and graded against official results. Historical results cannot predict future returns.
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
  if (location.pathname === "/reset") return <Reset />;
  if (auth === null) return <div className="boot"><Logo size={56} /></div>;
  return auth ? <Shell authRequired={authRequired} /> : <Login onDone={() => setAuth(true)} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
