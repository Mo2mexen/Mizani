import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { roleName, useAccount, useVersion } from "../session";

/** Who is signed in, and a settings button whose menu names the person and has Change password
 *  and Sign out (which also hands a shared tablet to the next person). */
export function AccountMenu({ onSignOut }: { onSignOut: () => void }) {
  const account = useAccount();
  const version = useVersion();
  const [isOpen, setIsOpen] = useState(false);
  const [isChanging, setIsChanging] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !wrap.current?.contains(e.target as Node)) setIsOpen(false);
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", close);
    };
  }, [isOpen]);

  return (
    <div className="account" ref={wrap}>
      <p className="account-who">
        <strong>{account.full_name}</strong>
        <span>{roleName(account)}</span>
      </p>
      <button
        type="button"
        className="btn btn-small btn-icon"
        aria-label="Account settings"
        title="Account settings"
        aria-expanded={isOpen}
        aria-controls="account-menu"
        onClick={() => setIsOpen(!isOpen)}
      >
        <GearIcon />
      </button>
      {isOpen && (
        <div id="account-menu" className="account-menu">
          <p className="account-menu-who">
            <strong>{account.full_name}</strong>
            <span>
              {roleName(account)}, signed in as {account.username}
            </span>
          </p>
          <button
            type="button"
            onClick={() => {
              setIsOpen(false);
              setIsChanging(true);
            }}
          >
            Change password
          </button>
          <button type="button" onClick={onSignOut}>
            Sign out
          </button>
          {version && <p className="account-menu-version">Mizani {version}</p>}
        </div>
      )}
      {isChanging && <ChangePassword onClose={() => setIsChanging(false)} />}
    </div>
  );
}

function GearIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

/** A modal dialog: the current password, then the new one twice. Other sign-ins of this account end. */
function ChangePassword({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isDone, setIsDone] = useState(false);
  const mismatch = again.length > 0 && next !== again;

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (mismatch) return;
    setIsBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      setIsDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not changed");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <dialog ref={dialog} className="panel dialog" aria-labelledby="password-title" onClose={onClose}>
      <h2 id="password-title" className="panel-title">
        Change your password
      </h2>
      {isDone ? (
        <>
          <p className="ok" role="status">
            Password changed. If you were signed in anywhere else, that sign-in has ended.
          </p>
          <button type="button" className="btn btn-primary" onClick={() => dialog.current?.close()} autoFocus>
            Done
          </button>
        </>
      ) : (
        <form onSubmit={handleSubmit}>
          <label className="field" htmlFor="pw-current">
            <span className="field-label">Current password</span>
            <input
              id="pw-current"
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              autoComplete="current-password"
              required
              autoFocus
            />
          </label>
          <label className="field" htmlFor="pw-new">
            <span className="field-label">New password</span>
            <input
              id="pw-new"
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password"
              minLength={10}
              required
            />
            <span className="field-note">At least 10 characters, without your user name.</span>
          </label>
          <label className="field" htmlFor="pw-again">
            <span className="field-label">Type it again</span>
            <input
              id="pw-again"
              type="password"
              value={again}
              onChange={(e) => setAgain(e.target.value)}
              autoComplete="new-password"
              aria-invalid={mismatch}
              required
            />
          </label>
          {mismatch && <p className="error">The two new passwords are not the same.</p>}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="decision-row">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isBusy || !current || next.length < 10 || next !== again}
            >
              {isBusy ? "Saving…" : "Change password"}
            </button>
            <button type="button" className="btn" onClick={() => dialog.current?.close()}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
