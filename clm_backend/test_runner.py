from django.test.runner import DiscoverRunner


class SupabaseKeepdbTestRunner(DiscoverRunner):
    """Test runner tuned for the project's Supabase-backed database."""

    keepdb = True

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('keepdb', True)
        super().__init__(*args, **kwargs)
