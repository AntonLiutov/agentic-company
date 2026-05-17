# Team Task Tracker

I want a small web app for managing team tasks.

Before using the app, each visitor must enter a unique username. This is only
for lightweight demo identity, not authentication.

I want the main experience to be a simple shared task dashboard. Users should be
able to create a task, see it appear in `To do` by default, have it assigned to
the creator by default, reassign it to another user or `No one`, and move it
through these statuses:

- `To do`
- `Blocked`
- `In progress`
- `In review`
- `Resolved`
- `Closed`

The board should be comfortable to use when there are many tasks. The task board
or main task area should work as one scrollable workspace, so users can browse a
large board smoothly while keeping the status layout easy to understand. Each
task should show who it is assigned to and a user-friendly date and time for the
latest status change, for example `May 16, 2026, 14:35`. A simple status history
is useful if it can be added without making the app complex.

Users should be able to filter the board by assignee, including tasks assigned
to a specific user and tasks assigned to `No one`.

The dashboard must be persistent and shared across users who access the page, so
if one user creates, assigns, or moves a task, other users can see the updated
state after refreshing or revisiting the app.

Please make the design modern, clear, and polished enough for a demo showcase.
Use internet research to reference strong examples of lightweight task-management
and productivity app design, then create an interface that feels immediately
useful, attractive, and easy to understand.

Use a refined dark product palette: a near-black ink foundation, layered graphite
panels, cool slate borders, bright cyan primary actions, and soft violet or
indigo highlights for focus, selection, and active states. Keep any warm colors
subtle and reserved for warnings or important status signals. The result should
feel premium, crisp, energetic, and demo-worthy while still being readable and
comfortable for real task work.

Use color intentionally: task cards should be easy to scan, status columns should
be visually distinct without becoming noisy, selected filters and active states
should be obvious, and timestamps and assignee labels should remain highly
readable. Use subtle depth, borders, spacing, and hover states to make the board
feel polished on both desktop and mobile.

This is for an internal demo, so please keep it simple. I do not need passwords,
roles, complex permissions, notifications, comments, file attachments, or a full
project management system.

Please make the app available in a browser after deploying it to Azure.

While developing, please qualitatively assess the design in a browser and improve
it until the dashboard feels polished, readable, responsive, and comfortable to
use with enough sample tasks to require scrolling. After deployment, repeat this
design-quality assessment against the live Azure app, along with the core task
flow checks.

You may use the current Azure integration and available Azure resources. If new
dev resources are needed for the demo, you may create them with clear names.
There are no access restrictions for this demo, but do not do anything unsafe or
unnecessary.

At the end, I need the working app link and a short, business-facing demo report.
Please keep it user-friendly and non-technical: summarize what the app does,
how a demo user can try it, and any important product limitations. Do not include
QA evidence, sprint/task breakdowns, implementation details, infrastructure
details, or other engineering notes. Screenshots, a simple visual flow, or a
small showcase-style summary are welcome if they help explain the app clearly.
