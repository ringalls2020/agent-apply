from .answers import FormAnswerSynthesizer, OpenAITextGenerator
from .apply import ApplyExecutionFlags, ApplyExecutor, ApplyService, SimulatedApplyExecutor
from .callbacks import CallbackEmitter
from .discovery import DiscoveryCoordinator
from .matching import MatchingService
from .playwright import PlaywrightApplyExecutor
from .store import JobIntelStore
from .. import services_legacy as _legacy

# Backward-compatible module attribute used by tests/monkeypatching.
time = _legacy.time

__all__ = [
    "JobIntelStore",
    "DiscoveryCoordinator",
    "CallbackEmitter",
    "MatchingService",
    "ApplyExecutor",
    "OpenAITextGenerator",
    "FormAnswerSynthesizer",
    "SimulatedApplyExecutor",
    "ApplyExecutionFlags",
    "PlaywrightApplyExecutor",
    "ApplyService",
]
