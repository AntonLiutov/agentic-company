# Web App MVP Requirements

Project name: Simple LLM Chat

Goal:
Create a local Streamlit app where a user can chat with an LLM.

Target user:
A solo builder testing simple assistant ideas locally.

Core features:
- User can enter a message
- App sends the message to an LLM
- App displays the assistant response
- Chat history stays visible during the session
- Missing API key shows a friendly setup message
- App can run with Docker Compose after local credentials are provided

Required configuration:
- OPENAI_API_KEY
- DEFAULT_MODEL

Preferred stack:
- Python
- Streamlit
- uv
- Docker Compose

Non-goals:
- Authentication
- Database persistence
- Multi-user support

Acceptance criteria:
- App starts locally with Streamlit
- App starts with `docker compose up --build`
- User can send a message and see a response
- Missing API key does not crash the app
- README explains uv setup, Docker Compose setup, and required environment variables
