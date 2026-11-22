from . import models


def post_init_hook(env):
    # Create the protected usage counter row once, at install time.
    from .models.limits import USAGE_KEY_SCANS
    env['elaf.health.usage']._get_row(USAGE_KEY_SCANS)
