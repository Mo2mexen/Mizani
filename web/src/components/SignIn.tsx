import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { IDLE_MINUTES, type SignedOutWhy } from "../session";
import type { Account } from "../types";

const WHY: Record<Exclude<SignedOutWhy, null>, string> = {
  idle: `You were signed out after ${IDLE_MINUTES} minutes without use. Sign in to continue.`,
  switch: "Signed out. The next person can sign in.",
  signed_out: "You signed out.",
  ended: "Your session ended (the time limit, or an admin signed you out). Sign in again to continue.",
};

/** The first screen on any device. There is no signup: an admin creates the account and gives a
 *  one-time password, which the person replaces here before anything else. */
export function SignIn({ why, onSignedIn }: { why: SignedOutWhy; onSignedIn: (account: Account) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [oneTime, setOneTime] = useState<{ account: Account; password: string } | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => first.current?.focus(), []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setIsBusy(true);
    setError(null);
    try {
      const account = await api.login(username.trim(), password);
      if (account.must_change) setOneTime({ account, password });
      else onSignedIn(account);
    } catch (err) {
      setPassword("");
      setError(err instanceof Error ? err.message : "Not signed in");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <main className="signin">
      <div className="signin-brand">
        <img className="brand-logo" src="/favicon.svg" alt="" width="30" height="30" />
        <h1 className="brand-name">Mizani</h1>
      </div>
      {oneTime ? (
        <ChooseOwnPassword oneTime={oneTime} onDone={onSignedIn} />
      ) : (
        <form className="panel signin-panel" onSubmit={handleSubmit} aria-labelledby="signin-title">
          <h2 id="signin-title" className="panel-title">
            Sign in
          </h2>
          {why && (
            <p className="signin-why" role="status">
              {WHY[why]}
            </p>
          )}
          <label className="field" htmlFor="signin-user">
            <span className="field-label">User name</span>
            <input
              ref={first}
              id="signin-user"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
            />
          </label>
          <label className="field" htmlFor="signin-password">
            <span className="field-label">Password</span>
            <input
              id="signin-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn-primary signin-go" disabled={isBusy || !username.trim() || !password}>
            {isBusy ? "Signing in…" : "Sign in"}
          </button>
          <p className="hint">No account yet? Your admin creates one and gives you a one-time password.</p>
        </form>
      )}
      <p className="signin-foot">Synthetic patients only. Mizani suggests; a signed-in nurse decides every colour.</p>
    </main>
  );
}

/** After a one-time password: the person picks their own before they can use Mizani. The
 *  one-time password they just typed is sent as the current one, so they type it only once. */
function ChooseOwnPassword({
  oneTime,
  onDone,
}: {
  oneTime: { account: Account; password: string };
  onDone: (account: Account) => void;
}) {
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [isShown, setIsShown] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = again.length > 0 && next !== again;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (mismatch) return;
    setIsBusy(true);
    setError(null);
    try {
      onDone(await api.changePassword(oneTime.password, next));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not saved");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <form className="panel signin-panel" onSubmit={handleSubmit} aria-labelledby="own-title">
      <h2 id="own-title" className="panel-title">
        Choose your own password
      </h2>
      <p className="hint">
        Welcome, {oneTime.account.full_name}. You signed in with a one-time password. Choose one only you know: at least
        10 characters, without your user name. A few words in a row are easy to remember.
      </p>
      <label className="field" htmlFor="own-new">
        <span className="field-label">New password</span>
        <input
          id="own-new"
          type={isShown ? "text" : "password"}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          autoComplete="new-password"
          minLength={10}
          required
          autoFocus
        />
      </label>
      <label className="field" htmlFor="own-again">
        <span className="field-label">Type it again</span>
        <input
          id="own-again"
          type={isShown ? "text" : "password"}
          value={again}
          onChange={(e) => setAgain(e.target.value)}
          autoComplete="new-password"
          aria-invalid={mismatch}
          aria-describedby={mismatch ? "own-mismatch" : undefined}
          required
        />
      </label>
      {mismatch && (
        <p id="own-mismatch" className="error">
          The two passwords are not the same.
        </p>
      )}
      <label className="check">
        <input type="checkbox" checked={isShown} onChange={(e) => setIsShown(e.target.checked)} />
        Show the passwords
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn-primary signin-go" disabled={isBusy || next.length < 10 || next !== again}>
        {isBusy ? "Saving…" : "Save and continue"}
      </button>
    </form>
  );
}
