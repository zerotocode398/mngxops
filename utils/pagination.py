from django.views.generic.list import MultipleObjectMixin


class PerPagePaginationMixin(MultipleObjectMixin):
    """Provide per-page selection via ?per_page=10|20|50|100."""

    default_paginate_by = 10
    per_page_options = (10, 20, 50, 100)

    def get_paginate_by(self, queryset):
        raw = self.request.GET.get("per_page", self.default_paginate_by)
        try:
            per_page = int(raw)
        except (TypeError, ValueError):
            per_page = self.default_paginate_by

        if per_page not in self.per_page_options:
            per_page = self.default_paginate_by
        return per_page

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_obj = context.get("page_obj")
        if page_obj:
            context["per_page"] = page_obj.paginator.per_page
        else:
            context["per_page"] = self.default_paginate_by
        context["per_page_options"] = self.per_page_options
        return context

