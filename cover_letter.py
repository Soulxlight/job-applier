import re
from typing import Dict


DEFAULT_TEMPLATE = """\
Dear Hiring Manager,

I am writing to express my interest in the {job_title} position at {company}. \
With my background in {field}, I am confident I would be a strong addition to your team.

{custom_paragraph}

Thank you for considering my application. I look forward to the opportunity to discuss \
how my skills align with {company}'s goals.

Sincerely,
{full_name}
"""


def render(template: str, job: Dict, profile: Dict) -> str:
    """Fill template variables from job listing and user profile."""
    variables = {
        'job_title': job.get('title', ''),
        'company': job.get('company', ''),
        'location': job.get('location', ''),
        'full_name': profile.get('full_name', ''),
        'first_name': profile.get('first_name', ''),
        'last_name': profile.get('last_name', ''),
        'email': profile.get('email', ''),
        'phone': profile.get('phone', ''),
        'field': profile.get('field', ''),
        'years_experience': profile.get('years_experience', ''),
        'linkedin': profile.get('linkedin', ''),
        'website': profile.get('website', ''),
        'custom_paragraph': profile.get('custom_paragraph', ''),
    }

    result = template
    for key, value in variables.items():
        result = result.replace(f'{{{key}}}', str(value) if value else '')

    # Remove any leftover unfilled {placeholders}
    result = re.sub(r'\{[^}]+\}', '', result)
    return result.strip()
