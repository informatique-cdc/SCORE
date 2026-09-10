from django.conf import settings
from django.shortcuts import redirect, render


def home(request):
    """Page vitrine publique servie à la racine.

    Un utilisateur déjà connecté n'a rien à y faire : il repart vers le
    tableau de bord, comme le faisait la redirection que cette vue remplace.
    """
    if request.user.is_authenticated:
        return redirect("dashboard-home")
    return render(request, "vitrine/home.html", {"gh": settings.VITRINE})
