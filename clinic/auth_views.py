"""Small, process-local throttle for the single-instance demo server."""
import hashlib

from django.contrib.auth.views import LoginView
from django.core.cache import cache


class ClinicLoginView(LoginView):
    template_name = 'registration/login.html'

    def attempt_key(self):
        # Do not retain passwords or cleartext login identifiers in cache keys.
        username = self.request.POST.get('username', '')[:150].strip().casefold()
        return 'login-attempts:' + hashlib.sha256(username.encode()).hexdigest()

    def post(self, request, *args, **kwargs):
        if cache.get(self.attempt_key(), 0) >= 5:
            form = self.get_form()
            form.add_error(None, 'Too many unsuccessful attempts. Please try again in 15 minutes.')
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self.attempt_key()
        cache.add(key, 0, timeout=900)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=900)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self.attempt_key())
        return super().form_valid(form)
