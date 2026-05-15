# Multi-Service Task Tracker Requirements

Project name: Multi-Service Task Tracker

Goal:
Create a small internal task tracker with an API service and a web UI service.

Target user:
A small team lead who wants a tiny shared task list for demo and planning work.

Core features:
- F1: Create and list tasks through the API and web UI
- F2: Mark tasks done through the API and web UI

Preferred stack:
- Python
- FastAPI
- Streamlit
- uv
- Docker Compose
- Azure Container Apps

Non-goals:
- Authentication
- Database persistence
- Multi-user permissions
- Complex project management workflows

Acceptance criteria:
- F1: API can create a task with a title
- F1: API can list tasks
- F1: Web UI can submit a task title
- F1: Web UI shows the current task list
- F2: API can mark a task as done
- F2: Web UI can toggle a task between open and done
- App runs locally with Docker Compose
- Deployment can update stable Azure dev resources after QA passes
