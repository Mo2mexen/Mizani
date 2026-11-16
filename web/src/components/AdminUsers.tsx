import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { clock, day } from "../format";
import { roleName, useAccount } from "../session";
import type { Account, Role } from "../types";

const ROLES: { role: Role; label: string; what: string }[] = [
  { role: "nurse", label: "Nurse", what: "Triage at the desk: intake, approve or override, re-check, handover, called in." },
  {
    role: "doctor",
    label: "Doctor",
    what: "The waiting room by priority, the handover inbox, and \"Seeing this patient\". Never changes a colour.",
  },
  { role: "admin", label: "Admin", what: "Accounts and settings. No clinical actions." },
];

type Handed = { name: string; password: string; isNew: boolean };

/** Admin › Users: who can sign in, with what role. Nobody signs up: an admin adds each person and
 *  hands over a one-time password. Accounts are deactivated, never deleted, so the records and the
 *  audit log keep naming the person who acted. */
export function AdminUsers() {
  const [people, setPeople] = useState<Account[] | null>(null);
  const [handed, setHanded] = useState<Handed | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    return api.users().then(setPeople, (err: unknown) => setError(err instanceof Error ? err.message : "Not loaded"));
  }

  useEffect(() => {
    void load();
  }, []);

  function changed(account: Account) {
    setPeople((list) => (list ?? []).map((p) => (p.id === account.id ? account : p)));
  }

  const active = (people ?? []).filter((p) => p.active).length;
  return (
    <main className="admin">
      <section className="panel" aria-labelledby="people-title">
        <h2 id="people-title" className="panel-title">
          People who can sign in
          {people && <span className="count">{active} active</span>}
        </h2>
        {handed && <OneTimePassword handed={handed} onDone={() => setHanded(null)} />}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {people === null && !error && <p className="empty">Loading accounts…</p>}
        {people && (
          <table className="people">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">User name</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">
                  <span className="visually-hidden">Manage</span>
                </th>
              </tr>
            </thead>
            {people.map((p) => (
              <Person
                key={p.id}
                person={p}
                onChanged={changed}
                onPassword={(password) => setHanded({ name: p.full_name, password, isNew: false })}
              />
            ))}
          </table>
        )}
      </section>
      <AddPerson
        onAdded={(account, password) => {
          setPeople((list) => [...(list ?? []), account]);
          setHanded({ name: account.full_name, password, isNew: true });
        }}
      />
    </main>
  );
}

function status(p: Account): { text: string; tone: "" | "warn" | "off" } {
  if (!p.active) return { text: "Deactivated", tone: "off" };
  if (p.locked_until && new Date(p.locked_until) > new Date())
    return { text: `Locked until ${clock(p.locked_until)} (wrong passwords)`, tone: "warn" };
  if (p.must_change) return { text: "Has a one-time password", tone: "warn" };
  return { text: "Active", tone: "" };
}

type Confirm = "reset" | "deactivate" | "signout" | null;

function Person({
  person,
  onChanged,
  onPassword,
}: {
  person: Account;
  onChanged: (account: Account) => void;
  onPassword: (password: string) => void;
}) {
  const me = useAccount();
  const [isOpen, setIsOpen] = useState(false);
  const [role, setRole] = useState<Role>(person.role);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isMe = person.id === me.id;
  const state = status(person);
  const panelId = `manage-${person.id}`;

  async function act(work: () => Promise<string | null>) {
    setIsBusy(true);
    setError(null);
    setNote(null);
    try {
      setNote(await work());
      setConfirm(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not saved");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <tbody className={person.active ? undefined : "is-off"}>
      <tr>
        <td data-label="Name">
          <strong dir="auto">{person.full_name}</strong>
          {isMe && <span className="count"> (you)</span>}
        </td>
        <td data-label="User name">{person.username}</td>
        <td data-label="Role">
          {roleName(person)}
          {person.chief && person.chief_by && person.chief_at && (
            <span className="field-note person-chief">
              Chief flag given by {person.chief_by} on {day(person.chief_at)}
            </span>
          )}
        </td>
        <td data-label="Status" className={state.tone ? `status-${state.tone}` : undefined}>
          {state.text}
        </td>
        <td data-label="Last sign-in">
          {person.last_login_at ? `${day(person.last_login_at)}, ${clock(person.last_login_at)}` : "Never"}
        </td>
        <td className="person-manage">
          <button
            type="button"
            className="btn btn-small"
            aria-expanded={isOpen}
            aria-controls={panelId}
            onClick={() => setIsOpen(!isOpen)}
          >
            Manage
          </button>
        </td>
      </tr>
      {isOpen && (
        <tr className="person-panel">
          <td colSpan={6} id={panelId}>
            <div className="person-actions">
              <div className="person-group">
                <label className="field" htmlFor={`role-${person.id}`}>
                  <span className="field-label">Role</span>
                  <select
                    id={`role-${person.id}`}
                    value={role}
                    disabled={isMe || isBusy}
                    onChange={(e) => setRole(e.target.value as Role)}
                  >
                    {ROLES.map((r) => (
                      <option key={r.role} value={r.role}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                  <span className="field-note">
                    {isMe ? "You cannot change your own role; ask another admin." : ROLES.find((r) => r.role === role)?.what}
                  </span>
                </label>
                <button
                  type="button"
                  className="btn btn-small"
                  disabled={isMe || isBusy || role === person.role}
                  onClick={() =>
                    void act(async () => {
                      const updated = await api.setRole(person.id, role);
                      onChanged(updated);
                      return `${person.full_name} is now: ${roleName(updated)}.`;
                    })
                  }
                >
                  Change role
                </button>
              </div>
              {person.role === "doctor" && (
                <div className="person-group">
                  <p className="field-label">Chief doctor</p>
                  <p className="field-note">The chief doctor can upload a triage chart and activate it after its evaluation.</p>
                  <button
                    type="button"
                    className="btn btn-small"
                    disabled={isBusy}
                    onClick={() =>
                      void act(async () => {
                        const updated = await api.setChief(person.id, !person.chief);
                        onChanged(updated);
                        return updated.chief
                          ? `${person.full_name} is now the chief doctor.`
                          : `${person.full_name} no longer has the chief flag.`;
                      })
                    }
                  >
                    {person.chief ? "Remove the chief flag" : "Give the chief flag"}
                  </button>
                </div>
              )}
              <div className="person-group">
                <p className="field-label">Sign-in</p>
                <div className="decision-row">
                  <button type="button" className="btn btn-small" disabled={isBusy} onClick={() => setConfirm("reset")}>
                    Reset password
                  </button>
                  <button type="button" className="btn btn-small" disabled={isBusy} onClick={() => setConfirm("signout")}>
                    Sign out everywhere
                  </button>
                  {person.active ? (
                    <button
                      type="button"
                      className="btn btn-small btn-warn"
                      disabled={isMe || isBusy}
                      onClick={() => setConfirm("deactivate")}
                    >
                      Deactivate
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-small"
                      disabled={isBusy}
                      onClick={() =>
                        void act(async () => {
                          onChanged(await api.setActive(person.id, true));
                          return `${person.full_name} can sign in again.`;
                        })
                      }
                    >
                      Reactivate
                    </button>
                  )}
                </div>
              </div>
            </div>
            {confirm && (
              <div className="confirm-box" role="group" aria-label="Confirm">
                <p>
                  {confirm === "reset" &&
                    `Reset ${person.full_name}'s password? Their sign-ins end, and you get a one-time password to give them.`}
                  {confirm === "signout" && `Sign ${person.full_name} out on every device?`}
                  {confirm === "deactivate" &&
                    `Deactivate ${person.full_name}? They can no longer sign in. Their name stays on every record they made.`}
                </p>
                <div className="decision-row">
                  <button
                    type="button"
                    className={`btn btn-small ${confirm === "deactivate" ? "btn-warn" : "btn-primary"}`}
                    disabled={isBusy}
                    onClick={() =>
                      void act(async () => {
                        if (confirm === "reset") {
                          onPassword((await api.resetPassword(person.id)).one_time_password);
                          onChanged({ ...person, must_change: true, locked_until: null });
                          return null;
                        }
                        if (confirm === "signout") {
                          await api.signOutEverywhere(person.id);
                          return `${person.full_name} is signed out everywhere.`;
                        }
                        onChanged(await api.setActive(person.id, false));
                        return `${person.full_name} is deactivated.`;
                      })
                    }
                  >
                    {confirm === "reset" ? "Reset password" : confirm === "signout" ? "Sign out everywhere" : "Deactivate"}
                  </button>
                  <button type="button" className="btn btn-small" disabled={isBusy} onClick={() => setConfirm(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {note && (
              <p className="ok" role="status">
                {note}
              </p>
            )}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </td>
        </tr>
      )}
    </tbody>
  );
}

/** Shown once, after an account is added or a password reset. The server keeps only its hash. */
function OneTimePassword({ handed, onDone }: { handed: Handed; onDone: () => void }) {
  const code = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(handed.password);
      setCopied("yes");
    } catch {
      // No clipboard on a plain-HTTP address: select the text so it can be copied by hand
      const range = document.createRange();
      if (code.current) range.selectNodeContents(code.current);
      window.getSelection()?.removeAllRanges();
      window.getSelection()?.addRange(range);
      setCopied("no");
    }
  }

  return (
    <div className="otp" role="status" aria-live="polite">
      <p className="otp-title">
        {handed.isNew ? `${handed.name} can now sign in.` : `${handed.name}'s password was reset.`} Give them this
        one-time password:
      </p>
      <code ref={code} className="otp-code">
        {handed.password}
      </code>
      <p className="hint">
        It is shown only this once. They choose their own password when they first sign in.
        {copied === "yes" && " Copied."}
        {copied === "no" && " Selected: press Ctrl+C (or Cmd+C) to copy it."}
      </p>
      <div className="decision-row">
        <button type="button" className="btn btn-small" onClick={() => void copy()}>
          Copy
        </button>
        <button type="button" className="btn btn-small btn-primary" onClick={onDone}>
          Done, I have given it
        </button>
      </div>
    </div>
  );
}

function suggestedUsername(fullName: string): string {
  return fullName
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, ".")
    .replace(/^\.+|\.+$/g, "")
    .slice(0, 32);
}

function AddPerson({ onAdded }: { onAdded: (account: Account, password: string) => void }) {
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("nurse");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Shown as a hint and used only when the box is left empty: never typed into it, so letters
  // typed straight after leaving Full name are never joined onto it.
  const suggestion = suggestedUsername(fullName);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setIsBusy(true);
    setError(null);
    try {
      const made = await api.addUser(username.trim() || suggestion, fullName.trim(), role);
      onAdded(made.account, made.one_time_password);
      setFullName("");
      setUsername("");
      setRole("nurse");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not added");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <form className="panel add-person" onSubmit={handleSubmit} aria-labelledby="add-title">
      <h2 id="add-title" className="panel-title">
        Add a person
      </h2>
      <label className="field" htmlFor="add-name">
        <span className="field-label">Full name</span>
        <input
          id="add-name"
          dir="auto"
          value={fullName}
          placeholder="e.g. Amina Otieno"
          onChange={(e) => setFullName(e.target.value)}
          required
        />
        <span className="field-note">As records will show it. Two people cannot share a name: add a middle initial.</span>
      </label>
      <label className="field" htmlFor="add-username">
        <span className="field-label">User name</span>
        <input
          id="add-username"
          value={username}
          placeholder={suggestion || "e.g. amina.otieno"}
          onChange={(e) => setUsername(e.target.value)}
          autoCapitalize="none"
          spellCheck={false}
          required={!suggestion}
        />
        <span className="field-note">
          {suggestion && !username.trim() ? `Leave it empty to use ${suggestion}. ` : ""}What they type to sign in:
          lower-case letters, digits, dots or dashes.
        </span>
      </label>
      <label className="field" htmlFor="add-role">
        <span className="field-label">Role</span>
        <select id="add-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r.role} value={r.role}>
              {r.label}
            </option>
          ))}
        </select>
        <span className="field-note">{ROLES.find((r) => r.role === role)?.what}</span>
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn-primary" disabled={isBusy || !fullName.trim() || !username.trim()}>
        {isBusy ? "Adding…" : "Add person"}
      </button>
      <p className="hint">You get a one-time password to give them. The chief flag is given to a doctor under Manage.</p>
    </form>
  );
}
