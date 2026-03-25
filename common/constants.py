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