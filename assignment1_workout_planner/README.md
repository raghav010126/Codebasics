# Assignment 1 - Workout Plan Generator

A single-page Streamlit app that turns structured fitness inputs into a personalized
weekly workout plan using an LLM via the Groq API.

## Setup

```bash
cd assignment1_workout_planner
uv sync
cp .env.sample .env   # fill in GROQ_API_KEY
uv run streamlit run app.py
```

## Inputs

- Fitness goal
- Experience level
- Days available per week
- Equipment access
- Injuries / limitations (optional)

## Design notes

- `generate_workout_plan()` is a typed, pure function separate from the Streamlit UI so
  the prompt/API logic can be tested independently.
- The system prompt forces per-day structured Markdown output, enforces the stated
  equipment/day constraints, and adds a safety disclaimer whenever injuries are
  mentioned.
- Invalid inputs (e.g. 0 days) and Groq failures (auth, network, rate limit, empty
  response) are all caught and shown as friendly `st.error` messages instead of
  crashing the app.
