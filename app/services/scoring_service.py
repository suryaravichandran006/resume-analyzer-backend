import logging

logger = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Safely convert a value to float within a clamped range."""
    try:
        v = float(value)
        return max(min_val, min(max_val, v))
    except (TypeError, ValueError):
        return default


def calculate_final_score(interviewer_json: dict) -> float:
    """
    Calculate a weighted final score from the interviewer analysis JSON.
    Returns a value between 0.0 and 1.0 (rounded to 2 decimals).
    """
    try:
        assessment = interviewer_json.get("preliminary_assessment", {})
        tech_score = _safe_float(assessment.get("technical_fit_score", 0.5))
        exp_score = _safe_float(assessment.get("experience_fit_score", 0.5))

        resume_analysis = interviewer_json.get("resume_analysis", {})
        keyword_score_raw = _safe_float(
            resume_analysis.get("keyword_match_score", 5),
            default=5.0,
            min_val=0.0,
            max_val=10.0
        )
        keyword_score = keyword_score_raw / 10.0  # normalise to 0-1

        must_have_skills = interviewer_json.get("job_requirements", {}).get("must_have_skills", [])
        if must_have_skills:
            avg_skill_score = sum(
                _safe_float(skill.get("candidate_proficiency", 0.5))
                for skill in must_have_skills
            ) / len(must_have_skills)
        else:
            avg_skill_score = 0.5  # neutral when no skills listed

        # Weighted: tech 40%, experience 30%, keyword 20%, skills 10%
        final_score = (
            tech_score * 0.40
            + exp_score * 0.30
            + keyword_score * 0.20
            + avg_skill_score * 0.10
        )

        return round(max(0.0, min(1.0, final_score)), 2)

    except Exception as e:
        logger.error(f"Score calculation error: {e}")
        return 0.5  # Return neutral score on failure