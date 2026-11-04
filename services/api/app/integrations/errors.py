class IntegrationError(ValueError):
    """A deliberately credential-free operational diagnostic."""


class UnusableRecord(IntegrationError):
    """One upstream record the definition cannot use; the run leaves it out.

    Its message is one of the engine's fixed explanations, which name the
    definition's own fields and paths and never quote the record.
    """
