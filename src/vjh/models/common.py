"""Shared enumerations used across schemas and the database."""

from enum import StrEnum


class Language(StrEnum):
    EN = "en"
    DE = "de"
    IT = "it"
    NL = "nl"
    OTHER = "other"


class CEFRLevel(StrEnum):
    """CEFR levels plus 'native'. Order matters: see CEFR_ORDER."""

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"
    NATIVE = "native"


CEFR_ORDER = [CEFRLevel.A1, CEFRLevel.A2, CEFRLevel.B1, CEFRLevel.B2,
              CEFRLevel.C1, CEFRLevel.C2, CEFRLevel.NATIVE]


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"          # confirmed by the user
    SOURCE_ONLY = "source_only"    # appears in CV or spec, not yet confirmed
    TODO = "todo"                  # conflict or gap: must ask the user


class EvidenceSource(StrEnum):
    CV = "cv"                      # your original CV in cv/
    SPEC = "spec"                  # docs/SPECIFICATION.md Part 2
    CV_AND_SPEC = "cv_and_spec"
    USER = "user"                  # stated by the user in conversation


class RequirementKind(StrEnum):
    MANDATORY = "mandatory"
    DESIRABLE = "desirable"


class RequirementStatus(StrEnum):
    MET = "met"
    PARTIALLY_MET = "partially_met"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"            # never counts as met


class Recommendation(StrEnum):
    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


class ScoreCategory(StrEnum):
    EXCELLENT = "excellent"
    STRONG = "strong"
    GOOD_POTENTIAL = "good_potential"
    POSSIBLE = "possible"
    LOW_PRIORITY = "low_priority"


class JobStatus(StrEnum):
    NEW = "new"                    # just collected, Stage 1 not run yet
    RELEVANT = "relevant"          # passed Stage 1, waiting for AI evaluation
    NEEDS_TEXT = "needs_text"      # only a title/link (e.g. alert email): import the full ad
    FILTERED_OUT = "filtered_out"  # Stage 1 set it aside (reason stored; never deleted)
    DUPLICATE = "duplicate"        # same job as another row (duplicate_of)
    EVALUATED = "evaluated"
    EXPIRED = "expired"


class ApplicationStatus(StrEnum):
    NEW = "new"
    RECOMMENDED = "recommended"
    UNDER_REVIEW = "under_review"
    DOCUMENTS_GENERATED = "documents_generated"
    APPROVED = "approved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    ARCHIVED = "archived"


class DocumentType(StrEnum):
    CV = "cv"
    COVER_LETTER = "cover_letter"


class ApprovalStatus(StrEnum):
    DRAFT = "draft"
    QA_FAILED = "qa_failed"
    QA_PASSED = "qa_passed"
    APPROVED = "approved"
    REJECTED = "rejected"


class CollectionMethod(StrEnum):
    API = "api"
    RSS = "rss"
    HTML = "html"                  # permitted HTML parsing only
    EMAIL_ALERT = "email_alert"
    MANUAL = "manual"              # user-provided URL / text / file


class SalaryPeriod(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"
    HOURLY = "hourly"
