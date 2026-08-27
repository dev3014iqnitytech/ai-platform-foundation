"""
RAG Constants — Centralized configuration values for the RAG pipeline.

All hardcoded values extracted here for maintainability and configurability.
Import from this module instead of hardcoding values in service classes.
"""

# ─── Greeting Detection ───────────────────────────────────────────────────────

GREETING_PHRASES = frozenset({
    "hello", "hi", "hey", "greetings",
    "good morning", "good afternoon", "good evening",
    "who are you", "what can you do", "help"
})

GREETING_RESPONSE = (
    "Hello! I am your AI Document Assistant. Ask me any question about your "
    "indexed documents, and I'll extract and summarize the information for you."
)

# ─── Response Messages ────────────────────────────────────────────────────────

NO_RESULT_RESPONSE = (
    "I could not find relevant information in the provided documents "
    "to answer your query."
)

RETRIEVAL_ERROR_RESPONSE = (
    "An error occurred while connecting to the document vector database. "
    "Please try again."
)

GENERATION_ERROR_TEMPLATE = "Error during answer generation from provider '{}': {}"

NO_DETAILED_INFO_RESPONSE = (
    "I could not find detailed information in the indexed documents "
    "to answer your question."
)

# ─── Score Thresholds ─────────────────────────────────────────────────────────

DEFAULT_DOMAIN_SCORE_THRESHOLD = 0.20
DEFAULT_GENERIC_SCORE_THRESHOLD = 0.32

# ─── Spell Correction ────────────────────────────────────────────────────────

DEFAULT_FUZZY_MATCH_CUTOFF = 0.7
MIN_WORD_LENGTH_FOR_CORRECTION = 4
WORD_EXTRACTION_PATTERN = r'[a-zA-Z0-9]{3,}'

# ─── Domain Seed Vocabulary ──────────────────────────────────────────────────
# Base domain words always available regardless of ingested documents.

DOMAIN_SEED_VOCABULARY = frozenset({
    "product", "products", "category", "categories", "manufacturing",
    "plants", "plant", "timeline", "history", "historical", "accessories",
    "acquisition", "acquisitions", "equipment", "technology", "tractor",
    "tractors", "harvesting", "facility", "facilities", "corporation",
    "evolution", "portfolio", "milestones", "alliances", "strategic",
    "summary", "overview", "profile", "specifications", "operations",
    "regional", "global", "guidance", "telemetry", "fendtone", "variogrip",
    "cargo", "sectioncontrol", "lotus", "katana"
})

# ─── Query Expansion ─────────────────────────────────────────────────────────
# Keyword categories mapped to template strings.
# Each entry: (set of trigger keywords, template string with {query} placeholder)

QUERY_EXPANSION_TEMPLATES = [
    (
        {"product", "category", "categories", "offering", "item", "equipment", "brand"},
        "What are the main product categories, equipment offerings, tractors, "
        "and product lines detailed in the documents regarding {query}?"
    ),
    (
        {"plant", "manufacturing", "facility", "location"},
        "What are the manufacturing plants and facilities detailed in the "
        "documents regarding {query}?"
    ),
    (
        {"history", "startup", "background", "origin", "founded"},
        "What is the startup background, corporate history, founding, origin story, "
        "and company history detailed in the documents regarding {query}?"
    ),
    (
        {"timeline", "acquisition", "acquisitions", "m&a"},
        "What is the historical acquisition timeline and M&A milestones detailed in the "
        "documents regarding {query}?"
    ),
]

DEFAULT_EXPANSION_TEMPLATE = (
    "What details, features, specifications, and context are provided "
    "regarding '{query}' in the indexed documents?"
)

# ─── Answer Synthesis ─────────────────────────────────────────────────────────

STOP_WORDS = frozenset({
    "what", "which", "tell", "about", "is", "are", "the", "in", "for",
    "and", "info", "information", "details", "does", "do", "exist",
    "exists", "show", "me", "give", "list", "provided", "regarding",
    "specifications", "context", "indexed", "documents"
})

NOISE_PATTERNS = [
    r'(?i)\bfendt product & accessory portfolio\b',
    r'(?i)\bagco corporation brand portfolio & technical specifications guide\b',
    r'(?i)\bcore machinery products\b',
    r'(?i)\bproduct category\s+key models / series\s+technical description & target applications\b',
    r'(?i)\bgenuine accessories, attachments & precision tech\b',
    r'(?i)\baccessory group\s+offerings & modules\s+operational functionality\b',
    r'(?i)Page \d+ of \d+\s+CONFIDENTIAL & PROPRIETARY[^\n]*',
    r'(?i)\bAGCO STRATEGY GROUP\b',
]

MIN_SENTENCE_LENGTH = 20
MIN_SENTENCE_WORDS = 3
TOP_SENTENCES_PARAGRAPH_1 = 3
TOP_SENTENCES_PARAGRAPH_2_START = 3
TOP_SENTENCES_PARAGRAPH_2_END = 5

# ─── LLM Defaults ────────────────────────────────────────────────────────────

DEFAULT_SYSTEM_MESSAGE = "You are a helpful document assistant."
DEFAULT_LLM_TEMPERATURE = 0.0
