"""MediBot chat UI (Streamlit). Talks to the FastAPI backend; never touches data directly."""

import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
ALL_COLLECTIONS = ["general", "clinical", "nursing", "billing", "equipment"]
DEMO_USERS = [
    ("dr.mehta", "Doctor@123", "doctor"),
    ("nurse.priya", "Nurse@123", "nurse"),
    ("billing.ravi", "Billing@123", "billing_executive"),
    ("tech.anand", "Tech@123", "technician"),
    ("admin.sys", "Admin@123", "admin"),
]
ROLE_ICONS = {"doctor": "🩺", "nurse": "💉", "billing_executive": "🧾", "technician": "🛠️", "admin": "🛡️"}
SAMPLES = {
    "doctor": ["What is the first-line drug for type 2 diabetes?", "What is the standard dose of Vancomycin?"],
    "nurse": ["How often should a central line dressing be changed?", "Show me all insurance billing codes"],
    "billing_executive": ["How many claims are escalated?", "Which department has the most rejected claims?"],
    "technician": ["What does fault code F-12 mean on the infusion pump?", "Show me the drug formulary dosing table"],
    "admin": ["Which equipment category has the most open tickets?", "What is the ICD-10 package rate for I21.0?"],
}

st.set_page_config(page_title="MediBot", page_icon="🏥", layout="wide")


def api(method: str, path: str, **kwargs) -> httpx.Response:
    return httpx.request(method, f"{BACKEND_URL}{path}", timeout=180, **kwargs)


def logout() -> None:
    for k in ("token", "role", "username", "collections", "messages"):
        st.session_state.pop(k, None)


# ---------------------------------------------------------------------- login
def login_view() -> None:
    st.title("🏥 MediBot")
    st.caption("MediAssist Health Network - internal knowledge assistant with role-based access control")
    left, right = st.columns([1, 1], gap="large")

    with left:
        st.subheader("Sign in")
        with st.form("login"):
            user = st.text_input("Username", key="login_user")
            pw = st.text_input("Password", type="password", key="login_pw")
            if st.form_submit_button("Sign in", type="primary", width="stretch"):
                try:
                    r = api("POST", "/login", json={"username": user, "password": pw})
                except httpx.HTTPError:
                    st.error(f"Cannot reach the MediBot backend at {BACKEND_URL}. Is it running?")
                    return
                if r.status_code == 200:
                    b = r.json()
                    st.session_state.update(token=b["access_token"], role=b["role"], username=b["username"],
                                            collections=b["accessible_collections"], messages=[])
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

    with right:
        st.subheader("Demo accounts")
        st.caption("Click an account to fill the form, then press Sign in.")
        for u, p, role in DEMO_USERS:
            def fill(u=u, p=p):
                st.session_state.login_user, st.session_state.login_pw = u, p
            st.button(f"{ROLE_ICONS[role]}  {u}  -  {role}", on_click=fill, width="stretch", key=f"demo_{u}")


# ---------------------------------------------------------------------- chat
def sidebar() -> None:
    role, allowed = st.session_state.role, st.session_state.collections
    with st.sidebar:
        st.markdown(f"### {ROLE_ICONS[role]} {st.session_state.username}")
        st.markdown(f":blue-badge[Role: {role}]")
        st.markdown("**Document collections**")
        for c in ALL_COLLECTIONS:
            if c in allowed:
                st.markdown(f"- ✅ **{c}**")
            else:
                st.markdown(f"- 🔒 ~~{c}~~ _(no access)_")
        sql_ok = role in ("billing_executive", "admin")
        st.markdown("**Analytics (SQL)**")
        tables = {"billing_executive": "claims", "admin": "claims, maintenance_tickets"}.get(role)
        st.markdown(f"- {'✅ ' + tables if sql_ok else '🔒 no access'}")
        st.divider()
        st.markdown("**Try asking**")
        for i, q in enumerate(SAMPLES[role]):
            if st.button(q, key=f"sample_{i}", width="stretch"):
                st.session_state.pending = q
        st.divider()
        st.button("Sign out", on_click=logout, width="stretch")


def render_assistant(m: dict) -> None:
    if m.get("blocked"):
        st.warning(m["content"], icon="🔒")
        st.markdown(":red-badge[Blocked by access control]")
        return
    st.markdown(m["content"])
    label = "🗄️ SQL RAG" if m["retrieval_type"] == "sql_rag" else "🔎 Hybrid RAG"
    st.markdown(f":blue-badge[{label}]")
    if m.get("sources"):
        with st.expander(f"Sources ({len(m['sources'])})"):
            for i, s in enumerate(m["sources"], 1):
                st.markdown(f"**[{i}]** `{s['source_document']}` - {s['section_title']}  \n_collection: {s['collection']}_")
    if m.get("sql_query"):
        with st.expander("SQL query"):
            st.code(m["sql_query"], language="sql")
    if m.get("rerank"):
        with st.expander("Reranker scores"):
            st.dataframe(m["rerank"], hide_index=True, width="stretch")


def ask(question: str) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Searching your accessible documents..."):
            try:
                r = api("POST", "/chat", json={"question": question},
                        headers={"Authorization": f"Bearer {st.session_state.token}"})
            except httpx.HTTPError:
                st.error("Cannot reach the MediBot backend.")
                return
        if r.status_code == 401:
            logout()
            st.rerun()
        if r.status_code != 200:
            detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            st.error(f"Request failed ({r.status_code}): {detail}")
            return
        b = r.json()
        msg = {"role": "assistant", "content": b["answer"], "sources": b["sources"], "retrieval_type": b["retrieval_type"],
               "blocked": b["blocked"], "sql_query": b["sql_query"], "rerank": b["rerank"]}
        render_assistant(msg)
    st.session_state.messages.append(msg)


def chat_view() -> None:
    sidebar()
    st.title("🏥 MediBot")
    st.caption(f"Signed in as **{st.session_state.username}** ({st.session_state.role}). "
               "Answers come only from the collections you are authorised to read.")
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            if m["role"] == "assistant":
                render_assistant(m)
            else:
                st.markdown(m["content"])
    pending = st.session_state.pop("pending", None)
    question = st.chat_input("Ask about protocols, drugs, billing codes, equipment, policies...") or pending
    if question:
        ask(question)


if "token" in st.session_state:
    chat_view()
else:
    login_view()
