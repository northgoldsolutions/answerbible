# theology_gate.py
"""
Answers in Faith — Theological Evidence Gate
Implements 12 non-negotiable blockers.
"""

from typing import List, Dict
from models import Claim, ClaimType, DoctrinalCategory, ReviewStatus, Production

MANUAL_REVIEW_TOPICS = {
    DoctrinalCategory.GENESIS_6,
    DoctrinalCategory.SHEOL_AFTERLIFE,
    DoctrinalCategory.SPIRITUAL_WARFARE,
    DoctrinalCategory.DEMONS,
    DoctrinalCategory.ELECTION,
    DoctrinalCategory.END_TIMES,
    DoctrinalCategory.DIVORCE_REMARRIAGE,
    DoctrinalCategory.WOMEN_MINISTRY,
    DoctrinalCategory.PROPHECY_DATING,
}

OCCULT_KEYWORDS = [
    "numerology", "astrology", "zodiac", "horoscope", "tarot",
    "spirit guide", "manifestation ritual", "fortune telling",
    "divination", "palm reading", "psychic", "channeling",
    "law of attraction", "speak it into existence", "decree and declare",
]

UNVERIFIABLE_REVELATION = [
    "god told me", "god spoke to me", "the lord told me",
    "i heard god say", "god revealed to me", "the spirit told me",
    "i have a word from the lord", "thus saith the lord",
]

DATE_SETTING = [
    "the end will come", "rapture will happen", "antichrist is",
    "mark of the beast is", "tribulation starts", "jesus returns on",
]

GOD_CHARACTER_VIOLATIONS = [
    "god hates everyone", "god enjoys suffering", "god causes evil",
    "god is cruel", "god is arbitrary", "god delights in condemnation",
]

GOSPEL_ERRORS = [
    "god helps those who help themselves",
    "be a good person", "try harder", "clean yourself up first",
]


class TheologyGateResult:
    def __init__(self):
        self.passed = True
        self.violations: List[Dict] = []
        self.warnings: List[Dict] = []
        self.requires_manual = False

    def block(self, rule: str, claim_id: str, detail: str, severity: str = "error"):
        self.passed = False
        entry = {"rule": rule, "claim_id": claim_id, "detail": detail, "severity": severity}
        if severity == "error":
            self.violations.append(entry)
        else:
            self.warnings.append(entry)

    def flag_manual(self, reason: str):
        self.requires_manual = True
        self.warnings.append({"rule": "MANUAL_REVIEW_REQUIRED", "detail": reason})


def run_theology_gate(production: Production, claims: List[Claim]) -> TheologyGateResult:
    result = TheologyGateResult()

    # BLOCKER 0: Auto-flag sensitive categories
    if production.doctrinal_category in MANUAL_REVIEW_TOPICS:
        result.flag_manual(f"Category '{production.doctrinal_category.value}' always requires human review")

    # BLOCKER 1: Scripture Support
    for claim in claims:
        if not claim.source_reference or not claim.source_text:
            result.block("BLOCKER_1_SCRIPTURE_SUPPORT", claim.id,
                f"Claim lacks source: '{claim.claim_text[:80]}...'", "error")

    # BLOCKER 2: Context Check
    for claim in claims:
        if not claim.context or len(claim.context.strip()) < 50:
            result.block("BLOCKER_2_CONTEXT_CHECK", claim.id,
                f"Insufficient context for: {claim.source_reference}", "error")

    # BLOCKER 3: Original Language Overclaim
    for claim in claims:
        text_lower = (claim.claim_text + " " + claim.interpretation).lower()
        if "this word only means" in text_lower or "the hebrew word always means" in text_lower:
            if not claim.original_language or len(claim.original_language.strip()) < 10:
                result.block("BLOCKER_3_LANGUAGE_OVERCLAIM", claim.id,
                    f"Absolute language claim without lexical evidence: '{claim.claim_text[:80]}'", "error")

    # BLOCKER 4: Interpretation vs Fact
    for claim in claims:
        if claim.claim_type == ClaimType.SPECULATION:
            result.block("BLOCKER_4_SPECULATION_AS_FACT", claim.id,
                f"SPECULATION cannot be fact: '{claim.claim_text[:80]}'", "error")
        if not claim.claim_type:
            result.block("BLOCKER_4_UNLABELED_CLAIM", claim.id,
                "Claim missing type classification", "error")

    # BLOCKER 5: "God Said" Filter
    for claim in claims:
        combined = (claim.claim_text + " " + claim.interpretation).lower()
        for phrase in UNVERIFIABLE_REVELATION:
            if phrase in combined:
                if not claim.source_reference or "bible" not in combined:
                    result.block("BLOCKER_5_GOD_SAID_FILTER", claim.id,
                        f"Unverifiable revelation ('{phrase}'): '{claim.claim_text[:80]}'", "error")

    # BLOCKER 6: No Divination / Occult
    for claim in claims:
        combined = (claim.claim_text + " " + claim.interpretation).lower()
        for keyword in OCCULT_KEYWORDS:
            if keyword in combined:
                result.block("BLOCKER_6_NO_DIVINATION_OCCULT", claim.id,
                    f"Occult reference ('{keyword}'): '{claim.claim_text[:80]}'", "error")

    # BLOCKER 7: Prophecy Safety
    for claim in claims:
        combined = (claim.claim_text + " " + claim.interpretation).lower()
        for phrase in DATE_SETTING:
            if phrase in combined:
                result.flag_manual("Prophecy date-setting requires explicit approval")
                result.block("BLOCKER_7_PROPHECY_SAFETY", claim.id,
                    f"Prophecy safety violation ('{phrase}'): '{claim.claim_text[:80]}'", "error")

    # BLOCKER 8: Character of God
    for claim in claims:
        combined = (claim.claim_text + " " + claim.interpretation).lower()
        for violation in GOD_CHARACTER_VIOLATIONS:
            if violation in combined:
                result.block("BLOCKER_8_CHARACTER_OF_GOD", claim.id,
                    f"Character contradiction: '{claim.claim_text[:80]}'", "error")
        if claim.character_of_god_relevant and claim.confidence.value in ["low", "medium"]:
            result.warnings.append({"rule": "BLOCKER_8_CHARACTER_REVIEW", "claim_id": claim.id,
                "detail": "Character-of-God claim with medium/low confidence flagged"})

    # BLOCKER 9: Gospel Integrity
    if production.gospel_video:
        gospel_claims = [c for c in claims if c.gospel_relevant]
        if not gospel_claims:
            result.block("BLOCKER_9_GOSPEL_INTEGRITY", "production",
                "Gospel video contains no gospel-relevant claims", "error")
        for claim in gospel_claims:
            combined = (claim.claim_text + " " + claim.interpretation).lower()
            for error in GOSPEL_ERRORS:
                if error in combined:
                    result.block("BLOCKER_9_GOSPEL_DISTORTION", claim.id,
                        f"Potential gospel distortion ('{error}'): '{claim.claim_text[:80]}'", "error")

    # BLOCKER 10: Cross-Reference Validation
    for claim in claims:
        if claim.claim_type in [ClaimType.SCRIPTURE, ClaimType.STRONG_INFERENCE]:
            refs = claim.cross_references or []
            if len(refs) < 1:
                result.block("BLOCKER_10_CROSS_REFERENCE", claim.id,
                    f"SCRIPTURE/INFERENCE needs supporting passages. Found {len(refs)}.", "error")

    # BLOCKER 11: Confidence Scoring
    for claim in claims:
        if claim.confidence.value == "low" and claim.claim_type in [ClaimType.SCRIPTURE, ClaimType.STRONG_INFERENCE]:
            result.block("BLOCKER_11_LOW_CONFIDENCE", claim.id,
                f"Low confidence incompatible with {claim.claim_type.value}: '{claim.claim_text[:80]}'", "error")
        if claim.confidence.value == "low":
            result.warnings.append({"rule": "BLOCKER_11_QUALIFY", "claim_id": claim.id,
                "detail": f"Low confidence claim must be qualified: '{claim.claim_text[:80]}'"})

    # BLOCKER 12: Human Review Cannot Be Bypassed
    if result.requires_manual and result.passed:
        result.passed = False
        result.warnings.append({"rule": "BLOCKER_12_HUMAN_GATE",
            "detail": "Sensitive topic requires explicit human approval"})

    return result
