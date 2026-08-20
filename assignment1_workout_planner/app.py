"""Workout Plan Generator - a Streamlit app that turns structured fitness
inputs into a personalized weekly workout plan using the Groq API."""

import os

import streamlit as st
from dotenv import load_dotenv
from groq import APIError, APIConnectionError, RateLimitError, Groq

load_dotenv()

GOALS = ["Build muscle", "Lose fat", "General fitness", "Improve endurance"]
EXPERIENCE_LEVELS = ["Beginner", "Intermediate", "Advanced"]
EQUIPMENT_OPTIONS = ["No equipment", "Home dumbbells", "Full gym"]

SYSTEM_PROMPT = """You are an experienced, certified personal trainer who writes safe, \
practical weekly workout plans.

Rules you must always follow:
- Only use exercises that fit the stated equipment access. Never assume equipment the \
user doesn't have.
- Write exactly one training day for each day per week the user has available. Do not \
add extra training days, and do not silently drop days.
- Match volume and intensity to the user's experience level (go easier and simpler for \
beginners, more advanced programming for advanced users).
- If the user lists injuries or limitations, you MUST avoid exercises that would \
aggravate them and substitute safer alternatives. Add one short disclaimer line at the \
end recommending they consult a medical professional before starting - only when \
injuries/limitations were provided.
- Never make medical claims or diagnose. You are not a doctor.
- Format the output as Markdown with a "Day 1", "Day 2", ... heading for each training \
day, and under each heading a list of exercises with sets and reps. Keep it scannable, \
not a wall of text.
- Stay strictly on the topic of workout programming."""


def build_user_prompt(
    goal: str,
    experience: str,
    days_per_week: int,
    equipment: str,
    injuries: str,
) -> str:
    """Assemble the user-facing prompt describing the client's request."""
    lines = [
        f"Fitness goal: {goal}",
        f"Experience level: {experience}",
        f"Training days available per week: {days_per_week}",
        f"Equipment access: {equipment}",
    ]
    if injuries.strip():
        lines.append(f"Injuries / limitations to work around: {injuries.strip()}")
    else:
        lines.append("Injuries / limitations: none reported")
    lines.append(
        "\nWrite a complete weekly plan that respects every constraint above."
    )
    return "\n".join(lines)


def generate_workout_plan(
    goal: str,
    experience: str,
    days_per_week: int,
    equipment: str,
    injuries: str = "",
) -> str:
    """Build a prompt and call the Groq API to generate a weekly workout plan.

    Raises ValueError for invalid inputs and RuntimeError for API/response failures,
    so the caller can show a friendly message instead of crashing.
    """
    if goal not in GOALS:
        raise ValueError(f"Unknown fitness goal: {goal!r}")
    if experience not in EXPERIENCE_LEVELS:
        raise ValueError(f"Unknown experience level: {experience!r}")
    if equipment not in EQUIPMENT_OPTIONS:
        raise ValueError(f"Unknown equipment option: {equipment!r}")
    if days_per_week < 1 or days_per_week > 7:
        raise ValueError("Days available per week must be between 1 and 7.")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY in environment.")

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    user_prompt = build_user_prompt(goal, experience, days_per_week, equipment, injuries)

    try:
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
            max_tokens=2048,
        )
    except RateLimitError as exc:
        raise RuntimeError("Groq rate limit hit. Please wait a moment and try again.") from exc
    except APIConnectionError as exc:
        raise RuntimeError("Couldn't reach Groq. Check your network connection.") from exc
    except APIError as exc:
        raise RuntimeError(f"Groq API error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as a friendly message upstream
        raise RuntimeError(f"Unexpected error calling Groq: {exc}") from exc

    if not completion.choices:
        raise RuntimeError("Groq returned no response choices.")

    plan = completion.choices[0].message.content
    if not plan or not plan.strip():
        raise RuntimeError("Groq returned an empty plan.")

    return plan.strip()


def render_app() -> None:
    st.set_page_config(page_title="Workout Plan Generator", page_icon="🏋️")
    st.title("🏋️ Workout Plan Generator")
    st.caption("Tell us about yourself and get a personalized weekly workout plan.")

    if "plan" not in st.session_state:
        st.session_state.plan = None

    with st.form("workout_inputs"):
        goal = st.selectbox("Fitness goal", GOALS)
        experience = st.selectbox("Experience level", EXPERIENCE_LEVELS)
        days_per_week = st.number_input(
            "Days available per week", min_value=0, max_value=7, value=3, step=1
        )
        equipment = st.selectbox("Equipment access", EQUIPMENT_OPTIONS)
        injuries = st.text_area(
            "Injuries or limitations (optional)",
            placeholder='e.g. "bad knees", "no overhead pressing"',
        )
        submitted = st.form_submit_button("Generate Plan")

    if submitted:
        if days_per_week < 1:
            st.error("Please select at least 1 training day per week.")
        else:
            with st.spinner("Building your plan..."):
                try:
                    st.session_state.plan = generate_workout_plan(
                        goal=goal,
                        experience=experience,
                        days_per_week=int(days_per_week),
                        equipment=equipment,
                        injuries=injuries,
                    )
                except ValueError as exc:
                    st.error(f"Invalid input: {exc}")
                except RuntimeError as exc:
                    st.error(f"Couldn't generate a plan: {exc}")

    if st.session_state.plan:
        st.markdown("---")
        st.markdown(st.session_state.plan)
        st.download_button(
            "Download plan as .md",
            data=st.session_state.plan,
            file_name="workout_plan.md",
            mime="text/markdown",
        )


if __name__ == "__main__":
    render_app()
