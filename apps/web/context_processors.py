from copy import copy

from django.conf import settings

from apps.base.alpine import ibis_store

from .meta import absolute_url


def user_meta(request):
    return {"sidebar_collapsed": request.session.get("sidebar_collapsed", False)}


def project_meta(request):
    # modify these values as needed and add whatever else you want globally available here
    project_data = copy(settings.PROJECT_METADATA)
    project_data["TITLE"] = "{} | {}".format(
        project_data["NAME"], project_data["DESCRIPTION"]
    )

    return {
        "project_meta": project_data,
        "page_url": absolute_url(request.path),
        "page_title": "",
        "page_description": "",
        "page_image": "",
        "ibis_store": ibis_store,
    }
