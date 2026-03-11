from allauth.account.adapter import DefaultAccountAdapter


class DocuScoreAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        return "/dashboard/"
