from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def has_role(context, role):
    membership = context.get("membership")
    if not membership:
        return False
    return membership.role == role
