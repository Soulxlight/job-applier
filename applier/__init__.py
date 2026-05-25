from .linkedin import LinkedInApplier
from .indeed import IndeedApplier
from .generic import GenericApplier

APPLIERS = {
    'linkedin': LinkedInApplier,
    'indeed': IndeedApplier,
    'ziprecruiter': GenericApplier,
    'greenhouse': GenericApplier,
    'lever': GenericApplier,
}
