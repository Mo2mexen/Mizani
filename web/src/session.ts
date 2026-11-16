// Who is signed in, for every screen below the shell. The server checks the role on every
// request; the page only hides what a role cannot use.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, lastUseSentAt, SIGNED_OUT_EVENT } from "./api";
import type { Account } from "./types";

/** Matches the server's idle limit (auth.IDLE): a shared tablet signs out after this long without use. */
export const IDLE_MINUTES = 15;
const TELL_SERVER_MS = 60_000; // while someone uses the page, tell the server at most once a minute

export const SessionContext = createContext<Account | null>(null);

export function useAccount(): Account {
  const account = useContext(SessionContext);
  if (!account) throw new Error("useAccount outside a signed-in page");
  return account;
}

/** The version the server runs (pyproject.toml), for the footer; null until it answers. */
export function useVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    api.health().then(
      (health) => setVersion(health.version),
      () => undefined,
    );
  }, []);
  return version;
}

export function roleName(account: Account): string {
  if (account.role === "doctor") return account.chief ? "Chief doctor" : "Doctor";
  return account.role === "admin" ? "Admin" : "Nurse";
}

/** Why the sign-in screen is showing; null on a first visit. */
export type SignedOutWhy = "idle" | "signed_out" | "ended" | null;

export function useSession() {
  const [account, setAccount] = useState<Account | null>(null);
  const [isChecking, setIsChecking] = useState(true);
  const [why, setWhy] = useState<SignedOutWhy>(null);
  const current = useRef<Account | null>(null);
  const leaving = useRef(false); // our own sign-out is under way: a late 401 must not reword it
  useEffect(() => {
    current.current = account;
  }, [account]);

  useEffect(() => {
    api.me().then(setAccount, () => setAccount(null)).finally(() => setIsChecking(false));
  }, []);

  // Any 401 from the server: the session ended elsewhere (an admin, another tab, the 12-hour
  // limit), or the server's idle clock ran out first (it hears of use at most once a minute).
  useEffect(() => {
    const ended = () => {
      const idle = Date.now() - lastUseSentAt() >= IDLE_MINUTES * 60_000;
      if (current.current && !leaving.current) setWhy(idle ? "idle" : "ended");
      setAccount(null);
    };
    window.addEventListener(SIGNED_OUT_EVENT, ended);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, ended);
  }, []);

  const signOut = useCallback(async (reason: Exclude<SignedOutWhy, null>) => {
    leaving.current = true;
    try {
      await api.logout();
    } catch {
      // already signed out on the server: nothing to undo
    }
    setAccount(null);
    setWhy(reason);
  }, []);

  // Idle sign-out. A key, click or touch counts as use; the page's own polling does not.
  useEffect(() => {
    if (!account) return;
    let lastUse = Date.now();
    let told = Date.now();
    const used = () => {
      lastUse = Date.now();
      if (lastUse - told > TELL_SERVER_MS) {
        told = lastUse;
        api.me().catch(() => undefined); // keeps the server's session as alive as the page
      }
    };
    const kinds = ["pointerdown", "keydown", "wheel", "touchstart"] as const;
    kinds.forEach((kind) => window.addEventListener(kind, used, { passive: true }));
    const timer = window.setInterval(() => {
      if (Date.now() - lastUse > IDLE_MINUTES * 60_000) void signOut("idle");
    }, 15_000);
    return () => {
      kinds.forEach((kind) => window.removeEventListener(kind, used));
      window.clearInterval(timer);
    };
  }, [account, signOut]);

  const signIn = useCallback((signed: Account) => {
    leaving.current = false;
    setAccount(signed);
    setWhy(null);
  }, []);

  return { account, isChecking, why, signIn, signOut, setAccount };
}
