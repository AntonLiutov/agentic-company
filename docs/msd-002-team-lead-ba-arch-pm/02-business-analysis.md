# Business Analysis Work

## Role

**Business Analyst Agent**

## Mission

Transform raw product intent into precise, testable, operational requirements that downstream Architect, PM, Team Lead, Fullstack, and QA agents can use.

## Inputs

- raw user/project brief;
- existing example requirements;
- current platform constraints;
- supported project archetypes;
- existing delivery agent catalog;
- known hackathon/demo goals.

## Outputs

BA should produce:

```text
runs/<run-id>/business-analysis/
  requirements-spec.md
  user-stories.json
  acceptance-criteria.json
  business-rules.md
  edge-cases.md
  open-questions.md
```

## Requirements specification contract

The requirements spec should contain:

1. Product summary.
2. Primary users.
3. User goals.
4. Functional requirements.
5. Non-functional requirements.
6. Explicit exclusions.
7. Business rules.
8. Edge cases.
9. Open questions.
10. Acceptance summary.

## Example target product: Team FAQ Chat App

### Product summary

Build a small web application where users can ask questions against a managed FAQ knowledge base. Admins can add/edit FAQ entries. The app shows a chat-like experience, stores chat history locally, and can be deployed as a simple containerized app.

### Primary users

| User | Goal |
|---|---|
| General user | Ask questions and get answers from the FAQ knowledge base |
| Admin user | Add, edit, and review FAQ entries |
| Demo reviewer | Run the app, test the flow, and understand what was delivered |

### Functional requirements

#### FR-001 - FAQ knowledge base

The application must include a simple FAQ knowledge base with question/answer entries.

Acceptance criteria:

- The app starts with seed FAQ entries.
- Admin can create a new FAQ entry.
- Admin can edit an existing FAQ entry.
- FAQ entries are persisted locally or in an app-local data file/database.

#### FR-002 - Chat question flow

A user can ask a question in a chat-like interface.

Acceptance criteria:

- User can type a question.
- App returns an answer based on the FAQ content.
- If no good FAQ match is found, app returns a clear fallback response.
- The answer includes a reference to the matched FAQ entry when possible.

#### FR-003 - Chat history

The app keeps recent chat messages visible during the session.

Acceptance criteria:

- User can see previous messages in the current session.
- New messages append at the bottom.
- The UI remains usable with at least 30 messages.

#### FR-004 - Admin page

Admin can manage FAQ entries.

Acceptance criteria:

- Admin can open an admin page or section.
- Admin can add a new FAQ entry.
- Admin can update an existing FAQ entry.
- Changes affect future chat answers.

#### FR-005 - Operability

The generated app must be easy to run and test.

Acceptance criteria:

- README explains local run.
- README explains Docker run.
- Tests validate core FAQ matching behavior.
- Docker Compose can start the app.

## Business rules

1. FAQ content is the source of truth for answers.
2. The app must not pretend to know answers outside the FAQ scope.
3. Admin functionality can use a simple demo-only admin mode; full authentication is not required for the first milestone unless explicitly planned.
4. The app must be small enough to build, QA, deploy, and demo within the milestone.
5. Deployment is allowed only after QA passes.

## Edge cases

| Edge case | Expected behavior |
|---|---|
| Empty question | Show validation message |
| Very long question | Accept with limit or show clear validation |
| No FAQ entries | Show clear no-content state |
| No answer match | Return fallback answer |
| Duplicate FAQ question | Allow or warn, based on implementation choice documented in README |
| Invalid FAQ edit | Show validation message |
| Docker not available | QA/deployment should block with clear reason |
| API key missing | App should either use deterministic fallback or show setup instructions |

## Open questions for human / PM

1. Should the demo require a real LLM API key or include deterministic fallback?
2. Is authentication required in this milestone or can admin mode be simplified?
3. Should the target generated app use Streamlit-only or FastAPI + web UI?
4. Should deployment happen after each sprint or only after final sprint?
5. What is the maximum acceptable runtime/cost for one generated project run?

## BA acceptance

BA work is complete when:

- all functional requirements have acceptance criteria;
- open questions are explicit;
- edge cases are listed;
- downstream Architect and PM can use the artifacts without guessing product intent;
- QA can derive tests from acceptance criteria.
