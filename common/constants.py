import re as _re

# ---------------------------------------------------------------------------
# Shared filter constants used by all borgs
# ---------------------------------------------------------------------------

# Job title keywords — at least one must match (case-insensitive)
TITLE_INCLUDE_KEYWORDS = [
    "software", "engineer", "backend", "senior",
]

# Job title keywords — if any match the job is excluded (case-insensitive)
TITLE_EXCLUDE_KEYWORDS = [
    # Junior / entry-level signals
    "junior", "jr", "jr.", "entry level", "entry-level", "fresher", "graduate", "intern", "internship", "trainee", "apprentice",

    # Mid-level ambiguity (you said senior, so be strict)
    "associate", "mid-level", "mid level", "intermediate", "level 1", "level i", "l1", "level 2", "level ii", "l2",

    # Non-engineering / adjacent roles
    "qa", "tester", "test engineer", "sdet", "automation tester", "manual tester",
    "support", "technical support", "it support", "helpdesk",
    "sysadmin", "system administrator", "network engineer", "it engineer",
    "devops", "site reliability", "sre",  # only exclude if you want pure SWE
    "data", "business", "manager",
    "scrum master", "agile coach", "analyst", "quality", 

    # Non-software engineering domains
    "hardware", "embedded", "firmware",  # exclude if you're not targeting these
    "electrical", "mechanical", "civil",

    # Design / frontend-only (optional depending on your goal)
    "ui", "ux", "designer", "web designer", "frontend intern",

    # Non-full-time / contract noise
    "contract", "contractor", "temporary", "temp", "freelance", "part-time", "part time",

    # Management / leadership (if you want IC roles only)
    "manager", "director", "vp", "vice president", "head", "chief", "cto", "lead", "principal", "staff",

    # Non-tech fluff titles
    "consultant", "advisor", "specialist", "coordinator", "assistant",

    # Sales / marketing disguised as tech
    "sales", "pre-sales", "presales", "marketing", "growth", "seo",

    # Weird startup nonsense titles
    "ninja", "rockstar", "guru", "wizard", "evangelist", "hacker", "champion",

    # Academic / research roles (optional)
    "researcher", "research assistant", "phd", "postdoc", "fellow",

    # Language / tech-specific junior traps
    "wordpress", "shopify", "wix", "webflow",

    # Misc filters that often pollute results
    "trainer", "instructor", "faculty", "teacher"
]

# Location keywords used to detect India-based roles (case-insensitive)
INDIA_LOCATION_KEYWORDS = [
    "india",
    "bangalore", "bengaluru",
    "hyderabad",
    "pune",
    "mumbai",
    "delhi",
    "noida",
    "gurgaon", "gurugram",
    "chennai",
    "kolkata",
    "ahmedabad",
]

# Default search text sent to APIs that support server-side search
SEARCH_TEXT = "Senior Software Engineer"

# ---------------------------------------------------------------------------
# Description analysis — weighted keyword matching for relevance scoring
# ---------------------------------------------------------------------------

# Positive signals: keyword → weight
DESC_POSITIVE_KEYWORDS = {
    # Languages
    "java": 2, "python": 2,

    # AI / GenAI
    "generative ai": 3, "genai": 3, "large language model": 3,
    "rag": 3, "retrieval augmented generation": 3,
    "langchain": 2, "vector database": 2, "embeddings": 2,
    "prompt engineering": 2, "fine-tuning": 2, "ai agents": 3,
    "openai": 2, "llm orchestration": 3, "ai infrastructure": 3,

    # Backend / infrastructure
    "microservices": 3, "distributed systems": 3, "api": 3, "rest": 3,
    "grpc": 3, "graphql": 3, "event-driven": 3, "kafka": 3, "rabbitmq": 3,

    # Cloud / devops
    "aws": 2, "gcp": 2, "azure": 2, "kubernetes": 2, "k8s": 2,
    "docker": 2, "terraform": 2, "ci/cd": 2,

    # Data stores
    "sql": 2, "nosql": 2, "postgresql": 2, "mysql": 2, "redis": 2,
    "elasticsearch": 2, "dynamodb": 2, "mongodb": 2, "cassandra": 2,
    # Architecture / design
    "system design": 3, "scalability": 3, "concurrency": 3,
    "high availability": 3, "fault tolerance": 3, "caching": 3,
    "load balancing": 3,
}

# Negative signals: keyword → weight (subtracted from score)
DESC_NEGATIVE_KEYWORDS = {
    # Frontend-only
    "react": 2, "angular": 2, "vue": 2, "css": 2, "html": 2,

    # Mobile
    "ios": 2, "android": 2, "swift": 2, "objective-c": 2, "flutter": 2,

    # ML / AI focused
    "machine learning": 2, "deep learning": 2,
    "computer vision": 2, "model training": 2,

    # Non-engineering
    "sales": 3, "marketing": 3, "data science": 3, "data analyst": 3,
    "recruiting": 3,
}

# Experience patterns: (regex_pattern, bonus)
DESC_EXPERIENCE_PATTERNS = [
    (_re.compile(r"\b[5-9]\+?\s*years", _re.IGNORECASE), 3),
    (_re.compile(r"\b1[0-5]\+?\s*years", _re.IGNORECASE), 3),
    (_re.compile(r"\bsenior\b", _re.IGNORECASE), 2),
    (_re.compile(r"\bbackend\b", _re.IGNORECASE), 3),
]

# Minimum relevance score to keep a job (jobs below this are discarded)
DESC_SCORE_THRESHOLD = 20