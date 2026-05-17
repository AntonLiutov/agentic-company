# One-Shot Codex Prompt: Hackathon Console Polish

We need a fast hackathon demo polish pass for the `agentic-company` Streamlit/operator console.

Do not rewrite the core agent runtime.

Implement the highest-impact UI/product changes:

1. Add demo login/register with user isolation.
2. Persist users/projects/runs so refresh does not lose state.
3. Add projects page and project history.
4. Add public demo project visible to all users.
5. Add settings page for OpenAI API key and Codex/provider status.
6. Add modern dashboard and project detail pages.
7. Add business-friendly board:
   - To Do
   - In Progress
   - Review
   - QA
   - Done
   - Blocked
8. Add agent cards:
   - Coordinator
   - Requirements Analyst
   - Solution Architect
   - Delivery Planner
   - Delivery Lead
   - Builder
   - Quality Reviewer
   - Publisher
   - Release Reporter
9. Add artifact viewer:
   - markdown
   - json
   - csv
   - mermaid if easy
   - html report link/preview
10. Hide technical logs by default.
11. Add business-friendly live logs.
12. Add optional browser voice input for requirement textarea using Web Speech API if available.
13. Add optional “Format with AI” button to clean dictated text.
14. Add Gemini provider placeholder/minimal adapter if quick.
15. Add system check page:
   - DB
   - OpenAI key
   - Codex
   - Docker
   - Azure CLI
   - Internet
   - Gemini optional
   - Speech input support

Important:
- Do not break existing Head/BA/Architect/PM/Team Lead flow.
- Do not expose raw internal filenames as primary UI labels.
- Do not show secrets.
- Keep technical details behind “Show technical details”.
- If this is too much, prioritize:
  A. auth/user isolation/projects/history
  B. board/artifact viewer
  C. settings/system check
  D. voice/Gemini stretch

Use the current repository structure and existing console support.
Keep changes small and testable.
