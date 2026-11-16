import { useEffect, useState } from "react";
import { api } from "./api";
import { AccountMenu } from "./components/AccountMenu";
import { AdminModel } from "./components/AdminModel";
import { AdminUsers } from "./components/AdminUsers";
import { AuditDrawer } from "./components/AuditDrawer";
import { Charts } from "./components/Charts";
import { DoctorView } from "./components/DoctorView";
import { NurseView } from "./components/NurseView";
import { SignIn } from "./components/SignIn";
import { SessionContext, useSession, useVersion } from "./session";
import type { Account, Colour } from "./types";

type View = { id: string; label: string };

/** The screens each role can open. The server checks the role on every request; this only
 *  decides what is shown. A role with one screen gets no tabs. */
function viewsFor(account: Account): View[] {
  if (account.role === "admin")
    return [
      { id: "users", label: "Users" },
      { id: "model", label: "Model" },
    ];
  if (account.role === "doctor")
    return account.chief
      ? [
          { id: "waiting", label: "Waiting room" },
          { id: "charts", label: "Charts" },
        ]
      : [{ id: "waiting", label: "Waiting room" }];
  return [{ id: "triage", label: "Triage" }];
}

export function App() {
  const session = useSession();
  const { account } = session;

  if (session.isChecking) return <p className="signin-checking">Opening Mizani…</p>;
  if (!account) return <SignIn why={session.why} onSignedIn={session.signIn} />;
  // Signed in with a one-time password, then the page was reloaded: own password first
  if (account.must_change)
    return (
      <SignIn
        why={null}
        pending={account}
        onSignedIn={session.signIn}
        onSignOut={() => void session.signOut("signed_out")}
      />
    );;
  return (
    <SessionContext.Provider value={account}>
      <SignedIn
        key={account.id}
        account={account}
        onSwitchUser={() => void session.signOut("switch")}
        onSignOut={() => void session.signOut("signed_out")}
      />
    </SessionContext.Provider>
  );
}

function SignedIn({ account, onSwitchUser, onSignOut }: { account: Account; onSwitchUser: () => void; onSignOut: () => void }) {
  const views = viewsFor(account);
  const [view, setView] = useState(views[0].id);
  const [colours, setColours] = useState<Colour[]>([]);
  const [isShadow, setIsShadow] = useState(false);
  const [isAuditOpen, setIsAuditOpen] = useState(false);
  const version = useVersion();
  const isNurse = account.role === "nurse";

  // Colours come once, but keep asking until the API answers (it may still be starting).
  useEffect(() => {
    if (colours.length > 0 || account.role === "admin") return;
    const load = () => api.colours().then(setColours, () => undefined);
    void load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [colours.length, account.role]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img className="brand-logo" src="/favicon.svg" alt="" width="30" height="30" />
          <h1 className="brand-name">Mizani</h1>
          <span className="brand-sub">Triage co-pilot for the adult SATS chart. The nurse decides.</span>
        </div>
        {views.length > 1 && (
          <nav className="views" aria-label="Screens">
            {views.map((v) => (
              <button
                key={v.id}
                type="button"
                className="view-tab"
                aria-current={view === v.id ? "page" : undefined}
                onClick={() => setView(v.id)}
              >
                {v.label}
              </button>
            ))}
          </nav>
        )}
        {isNurse && (
          <div className="toggle-wrap">
            <label className="toggle">
              <input
                type="checkbox"
                checked={isShadow}
                onChange={(e) => setIsShadow(e.target.checked)}
                aria-describedby="shadow-help"
              />
              Shadow mode
            </label>
            <span className="info" tabIndex={0} aria-label="What is shadow mode?">
              ?
              <span role="tooltip" id="shadow-help" className="tip">
                <strong>Shadow mode: you choose first.</strong> For each patient you pick a colour before you see
                Mizani's, so it can't influence you. Then you decide as usual. Use it for training, or to check how
                often you and Mizani agree (shown in the audit log).
              </span>
            </span>
          </div>
        )}
        <button type="button" className="btn btn-small" onClick={() => setIsAuditOpen(true)} aria-expanded={isAuditOpen}>
          Audit log
        </button>
        <AccountMenu onSwitchUser={onSwitchUser} onSignOut={onSignOut} />
      </header>
      {view === "triage" && <NurseView colours={colours} isShadow={isShadow} />}
      {view === "waiting" && <DoctorView colours={colours} />}
      {view === "users" && <AdminUsers />}
      {view === "model" && <AdminModel />}
      {view === "charts" && <Charts />}
      <AuditDrawer isOpen={isAuditOpen} onClose={() => setIsAuditOpen(false)} />
      <footer className="foot">
        Synthetic patients only. Mizani suggests; a signed-in nurse approves every priority. Every number shows the rule
        it came from. Mizani never diagnoses or prescribes.
        {version && <span className="foot-version">Mizani {version}</span>}
      </footer>
    </div>
  );
}
