from __future__ import annotations

import pytest

from retrieval.reranker_model import verified_reranker_files
from retrieval.reranking import CrossEncoderScorer, QuestionTooLongError

pytestmark = pytest.mark.integration

QUESTION = "How does Flask sign the session cookie with the secret key?"

SIGNING_PASSAGE = (
    "# File: src/flask/sessions.py\n"
    "# Method: SecureCookieSessionInterface.get_signing_serializer\n"
    "def get_signing_serializer(self, app):\n"
    "    if not app.secret_key:\n"
    "        return None\n"
    "    return URLSafeTimedSerializer(app.secret_key, salt=self.salt)"
)

UNRELATED_PASSAGE = (
    "# File: src/flask/cli.py\n"
    "# Function: find_best_app\n"
    "def find_best_app(module):\n"
    "    for attr_name in ('app', 'application'):\n"
    "        app = getattr(module, attr_name, None)"
)

LONG_PASSAGE = "# File: pkg/long.py\n# Function: long\n" + "value = compute(value)\n" * 400


@pytest.fixture(scope="module")
def scorer() -> CrossEncoderScorer:
    return CrossEncoderScorer(verified_reranker_files())


def test_the_passage_that_answers_the_question_scores_higher(scorer):
    pair_scores = scorer.score_pairs(QUESTION, [UNRELATED_PASSAGE, SIGNING_PASSAGE])

    unrelated_score, signing_score = pair_scores.scores
    assert signing_score > unrelated_score


def test_only_a_passage_longer_than_the_window_is_reported_as_cut(scorer):
    pair_scores = scorer.score_pairs(QUESTION, [SIGNING_PASSAGE, LONG_PASSAGE])

    assert pair_scores.truncated == [False, True]


def test_scores_do_not_depend_on_the_batch_size(scorer):
    passages = [SIGNING_PASSAGE, UNRELATED_PASSAGE, LONG_PASSAGE]
    batched = CrossEncoderScorer(verified_reranker_files(), batch_size=8)

    single_scores = scorer.score_pairs(QUESTION, passages).scores
    batched_scores = batched.score_pairs(QUESTION, passages).scores

    assert batched_scores == pytest.approx(single_scores, abs=1e-4)


def test_a_question_longer_than_half_the_window_is_refused(scorer):
    long_question = "How does this work? " * 100

    with pytest.raises(QuestionTooLongError, match="at most 256"):
        scorer.score_pairs(long_question, [SIGNING_PASSAGE])
