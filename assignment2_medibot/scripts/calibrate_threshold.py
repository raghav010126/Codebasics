"""Calibrate RERANK_MIN_SCORE: top rerank score for in-domain vs out-of-domain questions."""
import logging
import medibot  # noqa: F401
from medibot.retrieval.rerank import rerank
from medibot.retrieval.search import search
from medibot.retrieval.store import get_client

logging.getLogger().setLevel(logging.ERROR)
IN = ["What does fault code F-12 mean?", "Vancomycin dose", "How many days of earned leave?",
      "What size cannula for a baby under 5kg?", "emergency pre-auth deadline", "ICD-10 J18.9 package rate",
      "When should I clean my hands?", "infusion pump drug library out of date"]
OUT = ["What is the weather in Mumbai today?", "How do I bake a chocolate cake?",
       "Who won the cricket world cup?", "Write a poem about the ocean", "What is the capital of France?",
       "Explain quantum entanglement"]
c = get_client()
def top(q): return rerank(q, search(q, "admin", k=10, client=c), top_k=1)[0].score
tin, tout = [top(q) for q in IN], [top(q) for q in OUT]
print("in-domain  top scores:", [round(x, 3) for x in tin], "min", round(min(tin), 3))
print("out-domain top scores:", [round(x, 3) for x in tout], "max", round(max(tout), 3))
c.close()
