from allauth.account.adapter import DefaultAccountAdapter


class ScoreAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        return "/dashboard/"
