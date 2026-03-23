# topic_jel_map.py
"""Map OpenAlex topic names to JEL codes via keyword matching.

OpenAlex assigns topics to works with a hierarchical taxonomy
(domain > field > subfield > topic). This module maps the topic
display names to JEL (Journal of Economic Literature) codes using
keyword matching, enabling JEL enrichment from paper metadata.
"""

# Ordered by specificity — more specific patterns checked first.
# Each tuple: (keyword_to_match_in_lowercase, list_of_jel_codes)
_KEYWORD_JEL_RULES: list[tuple[str, list[str]]] = [
    # J — Labor and Demographic Economics
    ("labor market", ["J"]),
    ("labour market", ["J"]),
    ("wage", ["J"]),
    ("employment effect", ["J"]),
    ("unemployment", ["J"]),
    ("immigration", ["J"]),
    ("demographic", ["J"]),
    ("fertility", ["J"]),
    ("pension", ["J", "H"]),
    ("human capital", ["J", "I"]),
    # F — International Economics
    ("international trade", ["F"]),
    ("trade and", ["F"]),
    ("trade flow", ["F"]),
    ("exchange rate", ["F"]),
    ("globalization", ["F"]),
    ("migration", ["J", "F"]),
    ("foreign direct investment", ["F"]),
    # E — Macroeconomics and Monetary Economics
    ("monetary policy", ["E"]),
    ("inflation", ["E"]),
    ("central bank", ["E"]),
    ("macroeconomic", ["E"]),
    ("business cycle", ["E"]),
    ("interest rate", ["E"]),
    # G — Financial Economics
    ("financial market", ["G"]),
    ("banking", ["G"]),
    ("stock market", ["G"]),
    ("asset pricing", ["G"]),
    ("corporate finance", ["G"]),
    ("credit risk", ["G"]),
    ("insurance market", ["G"]),
    ("portfolio", ["G"]),
    # H — Public Economics
    ("tax", ["H"]),
    ("public finance", ["H"]),
    ("public good", ["H"]),
    ("government spending", ["H"]),
    ("fiscal policy", ["E", "H"]),
    ("public debt", ["H", "E"]),
    # I — Health, Education, and Welfare
    ("health economics", ["I"]),
    ("education", ["I"]),
    ("welfare", ["I"]),
    ("poverty", ["I", "O"]),
    ("health care", ["I"]),
    ("schooling", ["I"]),
    # O — Economic Development, Innovation, Technological Change, and Growth
    ("economic development", ["O"]),
    ("economic growth", ["O"]),
    ("innovation", ["O"]),
    ("technological change", ["O"]),
    ("technology adoption", ["O"]),
    ("entrepreneurship", ["L", "O"]),
    # D — Microeconomics
    ("behavioral economics", ["D"]),
    ("consumer", ["D"]),
    ("household decision", ["D"]),
    ("auction", ["D"]),
    ("inequality", ["D", "I"]),
    ("game theory", ["C", "D"]),
    # L — Industrial Organization
    ("industrial organization", ["L"]),
    ("firm", ["L"]),
    ("market structure", ["L"]),
    ("competition", ["L"]),
    ("antitrust", ["L", "K"]),
    ("market power", ["L"]),
    # C — Mathematical and Quantitative Methods
    ("econometric", ["C"]),
    ("experimental economics", ["C"]),
    ("statistical", ["C"]),
    ("causal inference", ["C"]),
    # Q — Agricultural and Natural Resource Economics
    ("environmental", ["Q"]),
    ("agricultural", ["Q"]),
    ("natural resource", ["Q"]),
    ("climate", ["Q"]),
    ("energy", ["Q"]),
    # R — Urban, Rural, Regional, Real Estate, and Transportation Economics
    ("urban", ["R"]),
    ("housing", ["R"]),
    ("regional", ["R"]),
    ("transportation", ["R"]),
    ("real estate", ["R"]),
    # K — Law and Economics
    ("law and economics", ["K"]),
    ("crime", ["K"]),
    ("regulation", ["K", "L"]),
    ("legal", ["K"]),
    # N — Economic History
    ("economic history", ["N"]),
    ("historical", ["N"]),
    # P — Economic Systems
    ("political economy", ["P", "H"]),
    ("economic system", ["P"]),
    # M — Business Administration
    ("marketing", ["M"]),
    ("accounting", ["M"]),
    ("management", ["M"]),
]


def map_topic_to_jel(topic_name: str) -> list[str]:
    """Map a single OpenAlex topic name to JEL codes via keyword matching.

    Returns list of unique JEL codes (e.g. ["J", "F"]) or empty list if no match.
    """
    lower = topic_name.lower()
    codes: list[str] = []
    for keyword, jel_codes in _KEYWORD_JEL_RULES:
        if keyword in lower:
            for code in jel_codes:
                if code not in codes:
                    codes.append(code)
    return codes
