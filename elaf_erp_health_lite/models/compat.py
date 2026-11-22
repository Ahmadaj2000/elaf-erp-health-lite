"""Odoo 17 compatibility: translation helper with the 18/19 ``self.env._()``
semantics.

The standard Odoo 17 ``_()`` falls back to the language of the user when the
environment has no ``lang`` in its context (cron jobs, shell, tests), whereas
``self.env._()`` of Odoo 18/19 uses English in that case. The scan engine and
the scheduled-audit alerts must behave the same on every series, so this alias
takes the language from ``self.env.lang`` of the calling method (English when
unset) and otherwise behaves exactly like ``odoo._`` (module detection, named
or positional arguments, Markup escaping).
"""
from odoo.tools.translate import GettextAlias


class EnvGettextAlias(GettextAlias):

    def _get_lang(self, frame):
        s = frame.f_locals.get('self')
        env = getattr(s, 'env', None)
        if env is not None and hasattr(env, 'lang'):
            return env.lang or 'en_US'
        return super()._get_lang(frame)


_ = EnvGettextAlias()
