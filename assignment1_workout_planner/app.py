"""Workout Plan Generator - a Streamlit app that turns structured fitness
inputs into a personalized weekly workout plan using the Groq API."""

import os
import re

import streamlit as st
from dotenv import load_dotenv
from groq import APIError, APIConnectionError, RateLimitError, Groq

load_dotenv()

GOALS = ["Build muscle", "Lose fat", "General fitness", "Improve endurance"]
EXPERIENCE_LEVELS = ["Beginner", "Intermediate", "Advanced"]
EQUIPMENT_OPTIONS = ["No equipment", "Home dumbbells", "Full gym"]

DAY_HEADING_RE = re.compile(r"^##\s*(Day\s*\d+.*)$")
EXERCISE_LINE_RE = re.compile(r"^-\s+(.*)$")

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
- Format the output as Markdown. Each training day must start on its own line with a \
level-2 heading in exactly this form: "## Day 1", "## Day 2", etc (day number only, no \
extra text on that heading line). Under each day heading, list each exercise on its own \
line starting with "- " followed by the exercise name, then " — ", then sets and reps, \
e.g. "- Barbell Squat — 3 sets x 10 reps". Do not nest or indent exercise lines. Keep it \
scannable, not a wall of text.
- Stay strictly on the topic of workout programming."""

SWAP_SYSTEM_PROMPT = """You are an experienced, certified personal trainer helping a \
client swap a single exercise in their existing workout plan.

Rules you must always follow:
- Suggest exactly one replacement exercise that targets similar muscle groups to the \
original, but is a genuinely different movement (not just a minor variation in wording).
- Only suggest exercises that fit the stated equipment access.
- If the user has injuries or limitations, the replacement must avoid aggravating them.
- Never make medical claims or diagnose. You are not a doctor.
- Respond with exactly one line, no extra commentary, no markdown heading, no leading \
dash, in exactly this form: "Exercise Name — 3 sets x 10 reps"."""


def build_user_prompt(
    goal: str,
    experience: str,
    days_per_week: int,
    equipment: str,
    injuries: str,
    variation: bool = False,
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
    if variation:
        lines.append(
            "The client already has a plan for these same inputs and wants a fresh "
            "variation: choose a different exercise selection and/or ordering than a "
            "typical first plan, while still respecting every constraint above."
        )
    return "\n".join(lines)


def _call_groq(messages: list[dict], temperature: float, max_tokens: int) -> str:
    """Call the Groq chat completion API and return the response text.

    Raises RuntimeError with a friendly message on any API or response failure.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY in environment.")

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    try:
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort="low",
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

    text = completion.choices[0].message.content
    if not text or not text.strip():
        raise RuntimeError("Groq returned an empty response.")

    return text.strip()


def generate_workout_plan(
    goal: str,
    experience: str,
    days_per_week: int,
    equipment: str,
    injuries: str = "",
    variation: bool = False,
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

    user_prompt = build_user_prompt(
        goal, experience, days_per_week, equipment, injuries, variation=variation
    )
    return _call_groq(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9 if variation else 0.6,
        max_tokens=2048,
    )


def parse_exercises(plan: str) -> list[dict]:
    """Extract (line_index, day, exercise_text) for each exercise line in the plan."""
    exercises = []
    current_day = "Day"
    for index, line in enumerate(plan.splitlines()):
        day_match = DAY_HEADING_RE.match(line.strip())
        if day_match:
            current_day = day_match.group(1).strip()
            continue
        exercise_match = EXERCISE_LINE_RE.match(line.strip())
        if exercise_match:
            exercises.append(
                {"line_index": index, "day": current_day, "text": exercise_match.group(1).strip()}
            )
    return exercises


def swap_exercise(
    exercise_text: str,
    day: str,
    goal: str,
    experience: str,
    equipment: str,
    injuries: str,
) -> str:
    """Ask Groq for a single replacement exercise line for one entry in the plan."""
    user_prompt = "\n".join(
        [
            f"Fitness goal: {goal}",
            f"Experience level: {experience}",
            f"Equipment access: {equipment}",
            f"Injuries / limitations: {injuries.strip() or 'none reported'}",
            f"Training day: {day}",
            f"Exercise to replace: {exercise_text}",
            "\nSuggest one replacement exercise for this slot.",
        ]
    )
    replacement = _call_groq(
        messages=[
            {"role": "system", "content": SWAP_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
        max_tokens=300,
    )
    return replacement.splitlines()[0].strip().lstrip("- ")


def render_app() -> None:
    st.set_page_config(page_title="Workout Plan Generator", page_icon="🏋️")
    st.title("🏋️ Workout Plan Generator")
    st.caption("Tell us about yourself and get a personalized weekly workout plan.")

    if "plan" not in st.session_state:
        st.session_state.plan = None
    if "last_inputs" not in st.session_state:
        st.session_state.last_inputs = None

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
            inputs = {
                "goal": goal,
                "experience": experience,
                "days_per_week": int(days_per_week),
                "equipment": equipment,
                "injuries": injuries,
            }
            with st.spinner("Building your plan..."):
                try:
                    st.session_state.plan = generate_workout_plan(**inputs)
                    st.session_state.last_inputs = inputs
                except ValueError as exc:
                    st.error(f"Invalid input: {exc}")
                except RuntimeError as exc:
                    st.error(f"Couldn't generate a plan: {exc}")

    if st.session_state.plan:
        st.markdown("---")
        st.markdown(st.session_state.plan)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Regenerate"):
                with st.spinner("Building a new variation..."):
                    try:
                        st.session_state.plan = generate_workout_plan(
                            **st.session_state.last_inputs, variation=True
                        )
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(f"Couldn't regenerate the plan: {exc}")
        with col2:
            st.download_button(
                "⬇️ Download plan as .md",
                data=st.session_state.plan,
                file_name="workout_plan.md",
                mime="text/markdown",
            )

        exercises = parse_exercises(st.session_state.plan)
        if exercises:
            st.markdown("#### Swap an exercise")
            options = [f"{ex['day']}: {ex['text']}" for ex in exercises]
            selected = st.selectbox("Pick an exercise to swap", options)
            selected_exercise = exercises[options.index(selected)]

            if st.button("Swap this exercise"):
                with st.spinner("Finding a replacement..."):
                    try:
                        replacement = swap_exercise(
                            exercise_text=selected_exercise["text"],
                            day=selected_exercise["day"],
                            goal=st.session_state.last_inputs["goal"],
                            experience=st.session_state.last_inputs["experience"],
                            equipment=st.session_state.last_inputs["equipment"],
                            injuries=st.session_state.last_inputs["injuries"],
                        )
                        lines = st.session_state.plan.splitlines()
                        lines[selected_exercise["line_index"]] = f"- {replacement}"
                        st.session_state.plan = "\n".join(lines)
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(f"Couldn't swap that exercise: {exc}")


if __name__ == "__main__":
    render_app()
