# Guide: Create and Trigger a Failing Workflow

This guide is for a teammate setting up an intentionally failing GitHub Actions
workflow so we can test the AI CI/CD Agent end to end. When the workflow fails,
GitHub fires a `workflow_run` webhook at our agent, which fetches the logs and
analyzes the failure.

There are two parts:

- Part A: Create the failing workflow (already provided, but explained here).
- Part B: Push the code to GitHub.
- Part C: Trigger the workflow.

---

## Part A — The failing workflow

A failing workflow is just a normal GitHub Actions workflow with a step that
exits with a non-zero status. We've already added one at:

```
.github/workflows/failing-test.yml
```

It runs a Python assertion that is guaranteed to fail:

```yaml
- name: Run a test that fails on purpose
  run: |
    echo "Starting test suite..."
    echo "Running test_addition..."
    python -c "assert 1 + 1 == 3, 'Math is broken: expected 1 + 1 to equal 3'"
```

When that `assert` fails, the step exits non-zero, the job's `conclusion`
becomes `failure`, and GitHub sends the `workflow_run` event our agent listens
for.

### Key requirements for the workflow file

If you ever create your own, make sure it has:

1. A location under `.github/workflows/` ending in `.yml` or `.yaml`.
2. A trigger. We use both so it's easy to fire:
   ```yaml
   on:
     push:
       branches: [main]
     workflow_dispatch:   # lets you run it manually from the Actions tab
   ```
3. At least one step that fails (any command that exits non-zero works, e.g.
   `exit 1`, a failing test, or a bad assertion).

---

## Part B — Push the code to GitHub

From the project root, in a terminal:

```bash
# 1. Stage the code
git add .

# 2. Commit
git commit -m "Add failing workflow to test the AI CI/CD Agent"

# 3. Push to the main branch
git push origin main
```

Notes:
- Do not commit the `.env` file. It holds secret keys and is already listed in
  `.gitignore`. If `git status` ever shows `.env`, stop and tell the team before
  pushing.
- If this is a fresh clone with no remote set, you'll need to add it first:
  ```bash
  git remote add origin https://github.com/<owner>/<repo>.git
  git branch -M main
  git push -u origin main
  ```

---

## Part C — Trigger the workflow

You have two ways to make it run. Pushing to `main` (Part B) already triggers it
once. To run it again on demand:

### Option 1 — Manually from the GitHub UI (easiest)
1. Go to the repository on GitHub.
2. Click the **Actions** tab.
3. In the left sidebar, select **Failing Test**.
4. Click the **Run workflow** dropdown on the right, choose the `main` branch,
   and click the green **Run workflow** button.
5. Wait ~30 seconds. The run will appear with a red ❌ (failed).

### Option 2 — By pushing a commit
Any new commit pushed to `main` re-runs the workflow:

```bash
git commit --allow-empty -m "Trigger failing workflow"
git push origin main
```

---

## What happens next

When the run finishes as a failure:

1. GitHub sends a `workflow_run` event (with `conclusion: failure`) to the
   agent's webhook URL.
2. The agent detects the failure, downloads the failed job's logs, splits them
   into chunks, and runs the cascadeflow analysis to suggest a fix.

The person running the agent will watch the server logs for these markers:

```
🚨 Pipeline failure detected! Run ID: <id> in <owner>/<repo>
✅ Successfully fetched logs. Length: <n> characters.
📦 Parsed into <n> chunks via Cascade.
🛠️ cascadeflow analysis complete.
```

If those appear, the full pipeline worked.

---

## Quick checklist before triggering

- [ ] `.github/workflows/failing-test.yml` is pushed to GitHub.
- [ ] The agent server is running and the cloudflared tunnel is up on port 8000.
- [ ] The GitHub webhook Payload URL matches the current tunnel hostname and ends
      in `/webhook`.
- [ ] The webhook is subscribed to the **Workflow runs** event.
