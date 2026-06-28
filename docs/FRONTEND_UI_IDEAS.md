# Frontend & UI Ideas — AI CI/CD Agent

A guide for the teammate building the frontend. The backend already detects
pipeline failures, fetches logs, chunks them, and uses cascadeflow + Groq to
generate a suggested fix. The UI's job is to put a **human in the loop**: show
what the agent found, let a person review the suggested fix, and approve, edit,
or reject it before anything is acted on.

---

## 1. Guiding principle: human-in-the-loop

Automation proposes, humans decide. The agent should never silently apply a fix.
Every AI suggestion flows through a review state:

```
failure detected  ->  agent suggests fix  ->  HUMAN REVIEWS  ->  approve / edit / reject  ->  action
```

The UI is the "review and decide" layer. Design every screen around making that
decision fast and confident.

---

## 2. Core screens (MVP)

### A. Dashboard / Failure Feed
The home screen. A live list of pipeline failures the agent has processed.

Each row (a "failure card") shows:
- Repo name and run ID
- Time detected
- Short status badge: `New`, `Analyzed`, `Awaiting Review`, `Approved`, `Rejected`
- A one-line summary of the suggested fix
- A "from memory" indicator if Hindsight recalled a past fix (instant, no LLM)

Sort newest first. Make the `Awaiting Review` items visually prominent, those
need a human.

### B. Failure Detail / Review screen
The heart of the app. Opened when a user clicks a failure card. Two panes:

- **Left: the evidence.** The raw log (or the relevant chunk), with the error
  lines highlighted. Collapsible so it doesn't overwhelm.
- **Right: the agent's analysis.** The Markdown fix the agent produced (root
  cause + suggested fix), rendered nicely.

Action bar at the bottom, this is the human control:
- ✅ **Approve** — accept the fix as-is
- ✏️ **Edit & Approve** — tweak the fix text, then accept
- ❌ **Reject** — dismiss it (optionally with a reason)
- 🔁 **Re-analyze** — ask the agent to try again

### C. Memory / Hindsight viewer
A simple table of what the agent has learned: known error signatures and their
stored fixes. Lets a human curate memory, delete a bad fix, or edit one. This
makes the "it remembers" feature visible and trustworthy.

---

## 3. The backend needs new endpoints

Right now the backend only exposes `POST /webhook` (consumed by GitHub, not the
UI). For the frontend to work, the backend should grow a small REST API. Suggest
these to whoever owns the backend (contracts below are proposals, align before
building):

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET`  | `/api/failures` | List processed failures for the feed |
| `GET`  | `/api/failures/{run_id}` | Full detail: logs, chunks, suggested fix |
| `POST` | `/api/failures/{run_id}/approve` | Mark fix approved (optionally edited) |
| `POST` | `/api/failures/{run_id}/reject` | Mark fix rejected with a reason |
| `POST` | `/api/failures/{run_id}/reanalyze` | Re-run cascadeflow on this failure |
| `GET`  | `/api/memory` | List Hindsight entries |
| `DELETE` | `/api/memory/{key}` | Remove a stored fix |
| `GET`  | `/api/stream` | Server-Sent Events / WebSocket for live updates |

> Important: the current webhook handler computes the fix but doesn't persist
> failures anywhere the UI can read. The backend will need to store each
> processed failure (in `hindsight_db.json` or a small DB) so these `GET`
> endpoints have something to return.

### Suggested data shape for a failure

```json
{
  "run_id": 28327513895,
  "repo": "CICD-projecttt/CI-CD",
  "detected_at": "2026-06-28T21:30:00Z",
  "status": "awaiting_review",
  "log_excerpt": "AssertionError: Math is broken...",
  "suggested_fix": "### Root cause\n...\n### Fix\n...",
  "from_memory": false
}
```

---

## 4. Real-time updates

Failures arrive via webhook at unpredictable times, so the feed should update
without a manual refresh. Two options, simplest first:

- **Polling**: frontend calls `GET /api/failures` every few seconds. Trivial to
  build, fine for an MVP/hackathon.
- **Server-Sent Events (SSE)** or **WebSocket**: backend pushes a new-failure
  event the moment the webhook fires. Nicer UX, a bit more backend work.

Recommendation for the hackathon: start with polling, upgrade to SSE if time
allows.

---

## 5. Suggested tech stack

Pick what the frontend dev is fastest in. Some good fits:

- **Framework**: React (with Vite) or Next.js. Vue/SvelteKit also fine.
- **Styling**: Tailwind CSS for speed, or a component kit like shadcn/ui,
  Chakra, or Mantine for ready-made cards/badges/buttons.
- **Markdown rendering**: `react-markdown` (the agent's fix is Markdown).
- **Log/code display**: a syntax-highlight component, e.g. `react-syntax-highlighter`.
- **Data fetching**: TanStack Query (React Query) handles polling and caching well.
- **CORS**: the FastAPI backend will need `CORSMiddleware` enabled so the browser
  app can call it from a different origin/port.

---

## 6. UX details that build trust

- **Always show the evidence.** Never show a fix without the log that justifies
  it. Humans approve faster when they can see why.
- **Confidence / source cues.** Badge fixes that came from memory vs freshly
  generated. Note which model produced it (e.g. `llama-3.3-70b`).
- **Make reject cheap and approve deliberate.** Approve is the consequential
  action; consider a small confirm step or an "Edit & Approve" default.
- **Audit trail.** Show who approved/rejected and when. Accountability matters
  for anything touching CI/CD.
- **Empty and loading states.** "No failures yet, agent is watching." plus a
  spinner while analysis is in flight.

---

## 7. Suggested build order

1. Static Dashboard reading mock JSON (no backend yet).
2. Failure Detail screen with mock log + mock Markdown fix.
3. Wire to real `GET /api/failures` and `GET /api/failures/{run_id}`.
4. Add Approve / Reject / Edit actions (`POST` endpoints).
5. Add live updates (polling, then SSE).
6. Add the Hindsight memory viewer.

---

## 8. Stretch ideas

- One-click "Open a GitHub PR with this fix" button (backend uses PyGithub).
- Diff view if the fix proposes concrete file changes.
- Filters by repo, status, or date.
- Slack/Discord notification when a failure needs review.
- Metrics: fixes approved vs rejected, time-to-resolution, repeat failures
  caught by memory.

---

## Quick note to the frontend dev

The only backend endpoint that exists today is `POST /webhook`, and it's for
GitHub, not the UI. You and the backend owner should agree on the `/api/*`
contracts in section 3 first, then you can build the UI against mock data while
those endpoints get implemented in parallel. Start with the Dashboard and the
Review screen, those two deliver the core human-in-the-loop value.
