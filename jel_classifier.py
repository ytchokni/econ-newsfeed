"""JEL code classification via OpenAI structured outputs.

Reads researcher descriptions and classifies them into standard
JEL (Journal of Economic Literature) codes.
"""
from database import Database
from openai import OpenAI
from pydantic import BaseModel, field_validator
import logging
import os

OPENAI_MODEL = os.environ.get("OPENAI_MODEL")
_openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Standard top-level JEL categories for prompt context
_JEL_CATEGORIES = """A - General Economics and Teaching
B - History of Economic Thought, Methodology, and Heterodox Approaches
C - Mathematical and Quantitative Methods
D - Microeconomics
E - Macroeconomics and Monetary Economics
F - International Economics
G - Financial Economics
H - Public Economics
I - Health, Education, and Welfare
J - Labor and Demographic Economics
K - Law and Economics
L - Industrial Organization
M - Business Administration and Business Economics; Marketing; Accounting; Personnel Economics
N - Economic History
O - Economic Development, Innovation, Technological Change, and Growth
P - Economic Systems
Q - Agricultural and Natural Resource Economics; Environmental and Ecological Economics
R - Urban, Rural, Regional, Real Estate, and Transportation Economics
Z - Other Special Topics"""


class JelClassification(BaseModel):
    """A single JEL code assignment with reasoning."""
    code: str
    reasoning: str

    @field_validator("code", mode="before")
    @classmethod
    def uppercase_code(cls, v: object) -> str:
        return str(v).upper().strip()


class JelClassificationResult(BaseModel):
    """Wrapper for structured output — OpenAI requires a top-level object."""
    jel_codes: list[JelClassification]


def build_classification_prompt(first_name: str, last_name: str, description: str) -> str:
    """Build the LLM prompt for JEL code classification."""
    return f"""Classify the following economics researcher into JEL (Journal of Economic Literature) codes based on their bio/description.

Researcher: {first_name} {last_name}

Bio/Description:
{description}

Assign one or more top-level JEL codes from this list:

{_JEL_CATEGORIES}

Rules:
- Assign between 1 and 5 codes that best represent the researcher's primary fields.
- Only assign codes where the bio provides clear evidence.
- Provide brief reasoning for each code.
- If the bio is too vague to classify, return an empty list.
- Do NOT assign "Y - Miscellaneous Categories" unless truly nothing else fits."""


def classify_researcher(
    researcher_id: int,
    first_name: str,
    last_name: str,
    description: str,
) -> list[str]:
    """Use OpenAI to classify a researcher into JEL codes.

    Returns a list of JEL code strings (e.g. ["J", "F"]).
    """
    prompt = build_classification_prompt(first_name, last_name, description)
    logging.info(
        "Classifying %s %s (id=%d) into JEL codes using OpenAI (%s)",
        first_name, last_name, researcher_id, OPENAI_MODEL,
    )

    try:
        chat_completion = _openai_client.beta.chat.completions.parse(
            messages=[{"role": "user", "content": prompt}],
            model=OPENAI_MODEL,
            response_format=JelClassificationResult,
        )
        Database.log_llm_usage(
            "jel_classification",
            OPENAI_MODEL,
            chat_completion.usage,
            researcher_id=researcher_id,
        )

        message = chat_completion.choices[0].message
        if message.refusal:
            logging.warning(
                "Model refused JEL classification for %s %s: %s",
                first_name, last_name, message.refusal,
            )
            return []

        result = message.parsed
        if result is None:
            logging.error(
                "Failed to parse JEL structured output for %s %s",
                first_name, last_name,
            )
            return []

        codes = [c.code for c in result.jel_codes]
        logging.info(
            "Classified %s %s → %s",
            first_name, last_name, ", ".join(codes) or "(none)",
        )
        return codes
    except Exception as e:
        logging.error(
            "Error in OpenAI JEL classification for %s %s: %s: %s",
            first_name, last_name, type(e).__name__, e,
        )
        return []
