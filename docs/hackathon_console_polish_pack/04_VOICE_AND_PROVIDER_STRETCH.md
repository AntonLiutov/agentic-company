# Voice Input and Provider Stretch Tasks

## Voice Input

Implement browser speech recognition first.

### Browser API

Use:
- `window.SpeechRecognition`
- `window.webkitSpeechRecognition`

Behavior:
- mic button beside request textarea;
- click starts listening;
- recognized text appended to textarea;
- show interim/final text if easy;
- stop button;
- fallback message if unsupported.

Fallback:
“Voice input is not supported by this browser. Please type your request.”

Do not implement server audio transcription tonight unless trivial.

## Format With AI

Button:
- takes current textarea value;
- asks model to clean grammar and formatting;
- must preserve meaning;
- must not add requirements;
- shows preview.

Prompt:

```text
Clean up this dictated product request.
Preserve meaning.
Do not add new requirements.
Fix grammar, punctuation, and paragraph structure.
Return only the cleaned request.
```

## Gemini Provider

Add placeholder/minimal adapter.

### Environment

```text
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3-flash-preview
```

### Minimal use

Use Gemini only for:
- formatting request text;
- summarizing artifacts;
- optional cheap coordinator draft.

Do not use Gemini for:
- Codex execution;
- code edits;
- deployment.

## Provider Adapter Shape

```python
class ProviderAdapter:
    def generate_text(self, prompt: str, model: str | None = None) -> str:
        ...
```

Adapters:
- OpenAIAdapter
- GeminiAdapter optional

If Gemini dependencies are missing, show provider as “not configured”.
