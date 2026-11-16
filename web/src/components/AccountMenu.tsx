import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { roleName, useAccount } from "../session";

type Props = {
  onSwitchUser: () => void;
  onSignOut: () => void;
};

/** Who is signed in, a one-click "Switch user" for a shared tablet, and a small menu with
 *  "Change password" and "Sign out". */
export function AccountMenu({ onSwitchUser, onSignOut }: Props) {
  const account = useAccount();
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
      <button type="button" className="btn btn-small" onClick={onSwitchUser}>
        Switch user
      </button>
      <button
        type="button"
        className="btn btn-small"
        aria-expanded={isOpen}
        aria-controls="account-menu"
        onClick={() => setIsOpen(!isOpen)}
      >
        Account
      </button>
      {isOpen && (
        <div id="account-menu" className="account-menu">
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
        </div>
      )}
      {isChanging && <ChangePassword onClose={() => setIsChanging(false)} />}
    </div>
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
