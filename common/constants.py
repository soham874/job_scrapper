# ---------------------------------------------------------------------------
# Shared filter constants used by all borgs
# ---------------------------------------------------------------------------

# Job title keywords — at least one must match (case-insensitive)
TITLE_INCLUDE_KEYWORDS = [
    "software", "engineer", "backend", "senior",
]

# Job title keywords — if any match the job is excluded (case-insensitive)
TITLE_EXCLUDE_KEYWORDS = [
    "analyst", "manager", "director", "staff", "principal",
    "android", "frontend",
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
