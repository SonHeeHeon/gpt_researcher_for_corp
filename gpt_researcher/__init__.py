__all__ = ["GPTResearcher"]


def __getattr__(name):
    if name == "GPTResearcher":
        from .agent import GPTResearcher

        return GPTResearcher
    raise AttributeError(f"module 'gpt_researcher' has no attribute {name!r}")
