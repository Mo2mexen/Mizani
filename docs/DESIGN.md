# Mizani design system

How the nurse's page looks and behaves, so every new screen matches. The source of truth is `web/src/styles.css`; this file explains the choices. The page is a desk instrument for a triage nurse under queue pressure: calm, readable at arm's length, and nothing on it competes with the colour of the patient.

## Principles
1. **The SATS colour is the loudest thing on the screen.** Red, orange, yellow and green appear only where they mean a triage colour, and always with the colour's name printed next to them (never colour alone).
2. **Every number shows its source.** A score, sign or flag carries its rule or input in small monospace text under it.
3. **The nurse's steps are numbered; nothing else is.** 1 Intake, 2 Proposal, 3 Waiting room. The agent's steps are supporting detail in a section the nurse can hide.
4. **Nothing pretends to be saved.** Buttons show "Saving…", "Releasing…", "AI is drafting…" while they work; the result appears only when the server confirms it.
5. **Works offline.** System fonts only; no web fonts, no external assets.

## Colour tokens (`:root` in styles.css)
| Token | Value | Use |
|---|---|---|
| `--paper` | `#f3f1ea` | page background |
| `--panel` | `#fffdf8` | panels and cards |
| `--ink` | `#1b1a17` | text, primary buttons, rules |
| `--ink-soft` | `#57544c` | secondary text, notes, units |
| `--line` / `--line-strong` | `#d8d3c6` / `#a39c8b` | dividers, field borders |
| `--focus` | `#1d4ed8` | keyboard focus ring, model steps in the trace |
| `--danger` / `--danger-bg` | `#a3191f` / `#fdecea` | refusals, "nurse to confirm", re-check due |
| `--warn` / `--warn-bg` | `#8a4a00` / `#fff4e0` | re-measure highlights, reconnecting |
| `--ok` | `#1f6b35` | saved, FHIR written, the "agent is working" dot |
| `--sats-red/orange/yellow/green` | `#c3232b` `#e0661a` `#f1c232` `#2e7d3a` | triage colours only |

Text colours meet WCAG AA (4.5:1) on `--paper` and `--panel`. On orange and yellow chips the text is `--ink`; on red and green it is white.

## Type
- Faces: the system UI font for everything (`--font-ui`, with Noto Sans Arabic for Arabic); the system monospace (`--font-num`) only for sources, rule text, run ids and the agent's steps.
- Numbers: lining, tabular figures everywhere, so columns of vitals line up.
- Scale: base 15 px. Panel titles 1.05 rem bold; sub-titles 0.9 rem bold; field labels 0.85 rem semibold; notes and units 0.78 rem in `--ink-soft`; sources 0.72 rem monospace. The proposed colour is the one display size (about 2.5 rem, extra bold).
- Case: sentence case for all headings and buttons. Capitals only for the colour names (ORANGE) and the RE-CHECK DUE badge.
- Times: 12-hour with seconds, "3:13:48 PM", everywhere (`clock()` in format.ts).

## Layout
- Desk (1200 px and wider): 1 Intake | 2 Proposal (the widest column); 3 Waiting room full width below; the agent's steps below that.
- Tablet: the same two columns, narrower. Phone: everything stacks in the same order.
- Spacing: one fluid gap (`--gap`, 12–20 px) between panels; 0.9–1 rem padding inside panels; radius 4 px (`--radius`) on panels, fields and buttons.
- The waiting room never pages: one column per colour, a long column scrolls on its own.

## Components
- **Panel:** `--panel` background, 1 px `--line` border, soft shadow; title row with the step number in a black circle.
- **Buttons:** `.btn` (outlined), `.btn-primary` (ink fill, the one main action of a panel), `.btn-warn` (override), `.btn-small` (inside cards). A button beside a field is as tall as the field. Disabled buttons fade to 45 %. A card's toggle button (Details, Re-check, Handover) stays pressed (grey, inset) while its panel is open; clicking it again closes it.
- **Fields:** label above, unit in `--ink-soft` after the label, note under the field. Required fields say "required"; the vitals hint says they are optional. Selects use one drawn arrow with room on the right.
- **Colour band:** the proposal's colour as a left bar, the name in display size, the label, "Set by …" in plain words, then the rule in small monospace.
- **Flags:** left border in the flag's meaning (orange: re-measure; red: refused or ignored; blue: re-check), bold kind, plain message, source under it.
- **Badge:** RE-CHECK DUE in white on `--danger`, centred, on the card's title row.
- **Help:** a white "?" circle next to a control; hover or keyboard focus shows a dark tooltip with one bold line and two or three plain sentences.
- **Working state:** a green dot that breathes next to "The agent is working · 6 s", the current step on the line below. Still when the system asks for reduced motion.

## Words
- Name things by what the nurse does: "Start triage", "Approve ORANGE", "Draft handover with AI", "Release to doctor", "Next patient".
- Errors say what happened and what to do, in the page's voice: "Mizani could not write a usable draft (…). Click Draft handover again, or write the handover yourself."
- Empty states invite the next action: "No patient yet. Fill in the intake…".
- Patient text is shown as typed, right-to-left when it is Arabic (`dir="auto"`), and never as HTML.

## Accessibility
Every control has a label; the trace and the result have live regions; visible focus ring on everything; keyboard alone can run the whole flow (Enter submits, Escape closes the audit log); motion respects `prefers-reduced-motion`.
