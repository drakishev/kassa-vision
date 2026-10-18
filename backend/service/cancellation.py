"""Cooperative cancellation at video and model generation checkpoints."""
CANCELLED_MESSAGE = 'Analysis cancelled by user'


class AnalysisCancelled(Exception):
    pass


class CancellationCriteria:
    """Transformers-compatible stopping criterion; never returns a partial result."""
    def __init__(self, check_cancelled):
        self.check_cancelled = check_cancelled

    def __call__(self, input_ids, scores, **kwargs):
        self.check_cancelled()
        return False
